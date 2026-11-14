"""Outgoing email helpers."""
import logging

from django.conf import settings
from django.core.mail.backends.smtp import EmailBackend

logger = logging.getLogger(__name__)


def email_enabled():
    """True when real email can be sent (SMTP credentials configured)."""
    return bool(settings.EMAIL_HOST_PASSWORD)


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
