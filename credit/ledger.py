"""The AI credit ledger (batch 6 §2): ``record`` is called by the shared AI helpers for every call; ``spend_context``
says who and what it is for; ``check`` / ``allowed`` enforce the monthly limits.

Who pays (counting rules, owner Nov 21, 2026):
- every AI call a person starts (Ask Attnd, imports, Studio, theme, Fill from website, Add with AI, paste import)
  counts against that person's month (a visitor before an account: their visitor key, carried into the account on
  sign-up);
- background work for an event (place photos, plan suggestions, website watching, covers) counts against the
  event's owner (``background=True``);
- exempt (``counted=False``, their own safety caps): staff-only Prospects, DEMO / showcase / App Review sample events
  and CI.
"""
import contextvars
import hashlib
import logging
from contextlib import contextmanager
from datetime import datetime, timezone as dt_timezone
from decimal import Decimal

from django.conf import settings
from django.db.models import Sum
from django.utils import timezone

logger = logging.getLogger(__name__)

_context = contextvars.ContextVar('ai_spend_context', default=None)
EXEMPT_FEATURES = {'prospects', 'ci', 'maintenance'}
APP_REVIEW_CODES = {'FWFM2026', 'ATIS2026', 'DACN2026'}
FEATURE_LABELS = {
    'ask_attnd': 'Ask Attnd', 'import': 'AI imports', 'refresh': 'Website checks', 'paste': 'Paste / file imports',
    'theme': 'Colours', 'studio': 'Studio website', 'studio_images': 'Studio images', 'fill': 'Fill from website',
    'place_images': 'Place photos', 'plan_suggestions': 'Plan suggestions', 'covers': 'Home page covers',
    'prospects': 'Prospects (staff)', 'tags': 'Tags', 'vision': 'Image descriptions', 'other': 'Other',
}


class LimitReached(Exception):
    """This month's AI credit is used up (the message is friendly, for the person)."""


@contextmanager
def spend_context(*, user=None, event=None, feature=None, background=None, visitor=None, exempt=None):
    """Attribute AI calls made inside this block. Inner blocks inherit what they don't set."""
    outer = _context.get() or {}
    inner = dict(outer)
    for key, value in (('user', user), ('event', event), ('feature', feature), ('background', background),
                       ('visitor', visitor), ('exempt', exempt)):
        if value is not None:
            inner[key] = value
    token = _context.set(inner)
    try:
        yield inner
    finally:
        _context.reset(token)


def current():
    return _context.get() or {}


def visitor_key(request):
    """A visitor before an account: session key + IP, hashed (never stored raw)."""
    from attnd.ratelimit import client_ip
    if not request.session.session_key:
        request.session.save()
    raw = f'{request.session.session_key}|{client_ip(request)}'
    return hashlib.sha256(raw.encode()).hexdigest()[:40]


def _is_exempt_event(event):
    if event is None:
        return False
    code = (event.event_code or '').upper()
    demo = (getattr(settings, 'DEMO_EVENT_CODE', '') or '').upper()
    return bool(event.showcase or (demo and code == demo) or code in APP_REVIEW_CODES)


def record(*, cost, model='', provider='anthropic', input_tokens=0, output_tokens=0, request_id='', feature=None,
           user=None, event=None):
    """One AI call into the ledger (never raises: a failed write must not break the feature)."""
    try:
        from credit.models import AiSpend
        ctx = current()
        user = user or ctx.get('user')
        event = event or ctx.get('event')
        feature = feature or ctx.get('feature') or 'other'
        background = bool(ctx.get('background'))
        if user is None and background and event is not None:
            user = event.user  # background work for an event counts against its owner
        if user is not None and not getattr(user, 'is_authenticated', True):
            user = None
        exempt = bool(ctx.get('exempt')) or feature in EXEMPT_FEATURES or _is_exempt_event(event)
        visitor = ctx.get('visitor') if user is None else ''
        if callable(visitor):
            visitor = visitor()
        row = AiSpend.objects.create(
            user=user, visitor=visitor or '', event=event, feature=feature[:32],
            provider=(provider or '')[:16], model=(model or '')[:64], input_tokens=int(input_tokens or 0),
            output_tokens=int(output_tokens or 0), cost_usd=Decimal(str(cost or 0)).quantize(Decimal('0.00001')),
            request_id=(request_id or '')[:80], counted=not exempt, background=background)
        if row.counted and user is not None:
            warn(user)
        platform_alert()
        return row
    except Exception:
        logger.exception('AI spend could not be recorded')
        return None


# ---------------------------------------------------------------------------
# Limits
# ---------------------------------------------------------------------------

def month_start(now=None):
    now = now or timezone.now()
    return datetime(now.year, now.month, 1, tzinfo=dt_timezone.utc)


def next_month(now=None):
    start = month_start(now)
    return datetime(start.year + (start.month == 12), start.month % 12 + 1, 1, tzinfo=dt_timezone.utc)


def month_key(now=None):
    return month_start(now).strftime('%Y-%m')


def limits():
    from credit.models import AiLimits
    return AiLimits.load()


def monthly_limit(user):
    """The person's monthly limit in USD, or None for no limit."""
    from credit.models import AiLimitOverride
    override = AiLimitOverride.objects.filter(user=user).first()
    if override is not None:
        if override.unlimited:
            return None
        if override.monthly_usd is not None:
            return Decimal(override.monthly_usd)
    if getattr(user, 'is_staff', False) or getattr(user, 'is_superuser', False):
        return None  # staff: counted, never limited (an override can still set an amount)
    return Decimal(limits().monthly_default_usd)


def used_this_month(user):
    from credit.models import AiSpend
    total = (AiSpend.objects.filter(user=user, counted=True, created_at__gte=month_start())
             .aggregate(t=Sum('cost_usd'))['t'])
    return total or Decimal('0')


def used_by_visitor(key):
    from credit.models import AiSpend
    return AiSpend.objects.filter(visitor=key, user__isnull=True, counted=True).aggregate(t=Sum('cost_usd'))['t'] or Decimal('0')


def status(user):
    """{used, limit, left, percent, renews, unlimited} for the person's month."""
    limit = monthly_limit(user)
    used = used_this_month(user)
    renews = next_month()
    out = {'used': used, 'limit': limit, 'unlimited': limit is None, 'renews': renews,
           'renews_label': f'{renews:%b} {renews.day}'}
    if limit is None:
        return {**out, 'left': None, 'percent': 0}
    left = max(Decimal('0'), limit - used)
    return {**out, 'left': left, 'percent': int(min(100, (used / limit * 100) if limit else 100))}


def limit_message(user):
    s = status(user)
    return (f"You've used this month's AI credit (${s['limit']:.0f}). It renews on {s['renews_label']}. "
            'Everything else keeps working. Need more? Use Talk to a person in Ask Attnd.')


def allowed(user=None, visitor=None, event=None):
    """May this person (or visitor) start an AI call now? Exempt events are always allowed."""
    if _is_exempt_event(event):
        return True
    if user is not None and getattr(user, 'is_authenticated', False):
        s = status(user)
        return s['unlimited'] or s['used'] < s['limit']
    if visitor:
        return used_by_visitor(visitor) < Decimal(limits().visitor_allowance_usd)
    return True


def check(user=None, visitor=None, event=None):
    """Raise LimitReached with the person's message when the month is used up."""
    if not allowed(user, visitor, event):
        if user is not None and getattr(user, 'is_authenticated', False):
            raise LimitReached(limit_message(user))
        raise LimitReached("You've used the free AI try-out. Create a free account to keep going.")


def owner_allowed(event):
    """Background AI for an event (photos, suggestions, watching, covers): pauses while its owner is over the limit."""
    if event is None or event.user_id is None:
        return True
    return allowed(event.user, event=event)


# ---------------------------------------------------------------------------
# Warnings
# ---------------------------------------------------------------------------

def warn(user):
    """80 % and 100 % of the month: a chat line (status) and an email, each once a month."""
    from credit.models import AiCreditNotice
    s = status(user)
    if s['unlimited']:
        return
    for level in (100, 80):
        if s['percent'] >= level:
            _, created = AiCreditNotice.objects.get_or_create(user=user, month=month_key(), level=str(level))
            if created:
                _email_person(user, level, s)
            return


def _email_person(user, level, s):
    if not user.email:
        return
    if level >= 100:
        subject = "You've used this month's AI credit"
        body = (f"You've used this month's Attnd AI credit (${s['limit']:.0f}). It renews on {s['renews_label']}.\n\n"
                'Editing, publishing and notifications keep working; AI features (Ask Attnd, imports, Studio) pause '
                'until then. Reply to this email if you need more.')
    else:
        subject = "You've used 80% of this month's AI credit"
        body = (f"You've used ${s['used']:.2f} of this month's ${s['limit']:.0f} Attnd AI credit. It renews on "
                f"{s['renews_label']}.")
    _send(subject, body, [user.email])


def platform_total(since=None):
    from credit.models import AiSpend
    return AiSpend.objects.filter(created_at__gte=since or month_start()).aggregate(t=Sum('cost_usd'))['t'] or Decimal('0')


def platform_alert():
    """Email the owner (ADMIN_EMAILS) once a month when every AI call together passes the alert amount."""
    from credit.models import AiCreditNotice
    amount = Decimal(limits().platform_alert_usd)
    if not amount or platform_total() < amount:
        return
    _, created = AiCreditNotice.objects.get_or_create(user=None, month=month_key(), level='platform')
    if not created:
        return
    admins = [a[1] if isinstance(a, (list, tuple)) else a for a in getattr(settings, 'ADMINS', [])]
    if admins:
        _send(f'Attnd AI spend passed ${amount:.0f} this month',
              f'All AI calls together have cost ${platform_total():.2f} since the 1st. See the AI spend page.', admins)


def _send(subject, body, to):
    from django.core.mail import send_mail
    try:
        send_mail(subject=subject, message=body, from_email=None, recipient_list=list(to))
    except Exception:
        logger.exception('AI credit email failed')
