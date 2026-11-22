"""Outgoing email helpers."""
import logging
import re

from django.conf import settings
from django.core.mail.backends.smtp import EmailBackend

logger = logging.getLogger(__name__)


def email_enabled():
    """True when real email can be sent (SMTP credentials configured)."""
    return bool(settings.EMAIL_ENABLED)


def site_url(path=''):
    """Absolute URL on this deployment, for links in emails."""
    return settings.SITE_URL + path


def allowed(address, allow_list):
    address = address.strip().lower()
    if '<' in address:
        address = address.rsplit('<', 1)[1].rstrip('>')
    return any(address == entry or (entry.startswith('@') and address.endswith(entry)) for entry in allow_list)


class AllowListSMTPBackend(EmailBackend):
    """SMTP, but when EMAIL_ALLOWED_RECIPIENTS is set only those addresses (or @domains) get mail;
    other recipients are logged and dropped, so a dev deployment never emails real people."""

    def send_messages(self, email_messages):
        allow_list = settings.EMAIL_ALLOWED_RECIPIENTS
        if not allow_list:
            return super().send_messages(email_messages)
        kept = []
        for message in email_messages:
            for field in ('to', 'cc', 'bcc'):
                addresses = getattr(message, field)
                ok = [a for a in addresses if allowed(a, allow_list)]
                dropped = [a for a in addresses if a not in ok]
                if dropped:
                    logger.info('Not emailing %s (not in EMAIL_ALLOWED_RECIPIENTS): %s', dropped, message.subject)
                setattr(message, field, ok)
            if message.recipients():
                kept.append(message)
        return super().send_messages(kept) if kept else 0


# ---------------------------------------------------------------------------
# Every email the platform sends (batch 7 Track B): one branded HTML template + a plain-text alternative, a log row
# per recipient (platform_admin.EmailLog: never the body), and one-click unsubscribe (RFC 8058) for optional kinds.
# ---------------------------------------------------------------------------



OPTIONAL, TRANSACTIONAL, SECURITY, INTERNAL = 'optional', 'transactional', 'security', 'internal'

# kind → (label, category). Optional kinds are the ones a person turns off in Account (ConsolePreference.notify)
# or with the email's unsubscribe link; the others always go out.
KINDS = {
    'import_done': ('An AI import finished', OPTIONAL),
    'review_waiting': ('Website changes are waiting for review', OPTIONAL),
    'notification_sent': ('A scheduled notification went out', OPTIONAL),
    'ai_credit': ('AI credit warnings (80% and 100% of the month)', OPTIONAL),
    'site_form_entry': ('New entries in your website’s forms', OPTIONAL),
    'invite': ('Team invitation', TRANSACTIONAL),
    'receipt': ('Receipts and invoices', TRANSACTIONAL),
    'claim_approved': ('Event claim approved', TRANSACTIONAL),
    'site_review': ('Website review result', TRANSACTIONAL),
    'password_reset': ('Password reset', SECURITY),
    'sign_in_code': ('Sign-in code', SECURITY),
    'new_sign_in': ('New sign-in alert', SECURITY),
    'upgrade_request': ('Upgrade request (to the Attnd team)', INTERNAL),
    'claim_request': ('Event claim (to the Attnd team)', INTERNAL),
    'contact': ('Help request (to the Attnd team)', INTERNAL),
    'platform_alert': ('Platform alert (to the Attnd team)', INTERNAL),
}
OPTIONAL_KINDS = {k: label for k, (label, cat) in KINDS.items() if cat == OPTIONAL}
UNSUBSCRIBE_SALT = 'attnd.email.unsubscribe'
_CODE = re.compile(r'\b\d{6}\b')


def is_optional(kind):
    return KINDS.get(kind, ('', TRANSACTIONAL))[1] == OPTIONAL


def wants(user, kind):
    """Optional kinds are on unless the person turned them off; every other kind always goes out."""
    if not is_optional(kind):
        return True
    from console.models import ConsolePreference
    pref = ConsolePreference.objects.filter(user=user).values_list('notify', flat=True).first()
    return (pref or {}).get(kind, True)


def unsubscribe_token(user, kind):
    from django.core import signing
    return signing.dumps({'u': user.pk, 'k': kind}, salt=UNSUBSCRIBE_SALT, compress=True)


def read_unsubscribe_token(token):
    """(user, kind) or (None, None) for a bad token or a kind that can't be unsubscribed."""
    from django.contrib.auth.models import User
    from django.core import signing
    try:
        data = signing.loads(token, salt=UNSUBSCRIBE_SALT)
    except signing.BadSignature:
        return None, None
    kind = data.get('k') if isinstance(data, dict) else None
    if not is_optional(kind):
        return None, None
    return User.objects.filter(pk=data.get('u'), is_active=True).first(), kind


def set_wanted(user, kind, on):
    from console.models import ConsolePreference
    pref, _ = ConsolePreference.objects.get_or_create(user=user)
    notify = dict(pref.notify or {})
    notify[kind] = bool(on)
    pref.notify = notify
    pref.save(update_fields=['notify', 'updated_at'])


def _address(value):
    value = value.strip()
    return value.rsplit('<', 1)[1].rstrip('>').strip() if '<' in value else value


def render(kind, subject, *, heading=None, paragraphs=(), code=None, details=(), button=None, after=(),
           unsubscribe_url=None):
    """(text, html) of one email from its parts; both from the same template set (templates/emails/)."""
    from django.template.loader import render_to_string
    label, category = KINDS.get(kind, ('', TRANSACTIONAL))
    context = {
        'subject': subject, 'heading': heading or subject, 'paragraphs': [p for p in paragraphs if p],
        'code': code, 'details': list(details), 'button': button, 'after': [p for p in after if p],
        'kind_label': label, 'category': category, 'unsubscribe_url': unsubscribe_url,
        'preferences_url': site_url('/console/account/#emails') if category == OPTIONAL else None,
        'site_url': settings.SITE_URL, 'logo_url': site_url('/icon-96.png'),
    }
    text = render_to_string('emails/message.txt', context).strip() + '\n'
    html = render_to_string('emails/message.html', context)
    return text, html


def _log(kind, to, subject, status, message_id='', error='', user=None):
    try:
        from platform_admin.models import EmailLog
        EmailLog.objects.create(kind=kind, to=_address(to)[:254], subject=_CODE.sub('••••••', subject)[:200],
                                status=status, message_id=message_id.strip('<>')[:250], error=error[:300],
                                user=user if getattr(user, 'pk', None) else None)
    except Exception:  # logging never stops an email
        logger.exception('Could not log the email')


def send(kind, to, subject, *, heading=None, paragraphs=(), code=None, details=(), button=None, after=(),
         reply_to=None, attachments=(), from_email=None, fail_silently=True):
    """Send one kind of email to `to` (addresses). Optional kinds go one message per person (their own unsubscribe
    link, List-Unsubscribe + List-Unsubscribe-Post) and only to people who want them; the others go as one message.
    Every recipient gets an EmailLog row (sent / failed / blocked / logged / skipped). Returns how many were sent."""
    from django.contrib.auth.models import User
    from django.core.mail import EmailMultiAlternatives
    from django.core.mail.message import make_msgid
    to = [a for a in dict.fromkeys(a.strip() for a in to if a and a.strip())]
    if not to:
        return 0
    allow_list = settings.EMAIL_ALLOWED_RECIPIENTS
    blocked = [a for a in to if allow_list and not allowed(a, allow_list)]
    for address in blocked:
        _log(kind, address, subject, 'blocked')
    to = [a for a in to if a not in blocked]
    status = 'sent' if email_enabled() else 'logged'
    domain = _address(from_email or settings.DEFAULT_FROM_EMAIL).rsplit('@', 1)[-1] or 'attnd.app'

    def one(recipients, user=None):
        headers, unsubscribe_url = {}, None
        if user is not None and is_optional(kind):
            unsubscribe_url = site_url(f'/email/unsubscribe/{unsubscribe_token(user, kind)}/')
            headers['List-Unsubscribe'] = f'<{unsubscribe_url}>'
            headers['List-Unsubscribe-Post'] = 'List-Unsubscribe=One-Click'
        message_id = make_msgid(domain=domain)
        headers['Message-ID'] = message_id
        headers['X-Attnd-Kind'] = kind
        text, html = render(kind, subject, heading=heading, paragraphs=paragraphs, code=code, details=details,
                            button=button, after=after, unsubscribe_url=unsubscribe_url)
        message = EmailMultiAlternatives(subject, text, from_email, recipients, reply_to=reply_to, headers=headers)
        message.attach_alternative(html, 'text/html')
        for name, content, mimetype in attachments:
            message.attach(name, content, mimetype)
        try:
            message.send(fail_silently=False)
        except Exception as error:
            for address in recipients:
                _log(kind, address, subject, 'failed', message_id, type(error).__name__, user)
            if not fail_silently:
                raise
            logger.exception('Email (%s) failed', kind)
            return 0
        for address in recipients:
            _log(kind, address, subject, status, message_id, '', user)
        return len(recipients)

    if not is_optional(kind):
        return one(to) if to else 0
    sent = 0
    for address in to:
        user = User.objects.filter(email__iexact=_address(address), is_active=True).first()
        if user is not None and not wants(user, kind):
            _log(kind, address, subject, 'skipped', user=user)
            continue
        sent += one([address], user)
    return sent


def admin_emails():
    return [a[1] if isinstance(a, (list, tuple)) else a for a in getattr(settings, 'ADMINS', [])]
