"""Plans (owner, Nov 21, 2026): **Free** (an unpaid demo: 20 phones), **Standard** (paid: no phone limit, nearby
listing), **Studio** (Standard + publishing the event's website). Prices live in `PlatformSettings` (the Platform
admin page). No checkout yet (Stripe later, ROADMAP): an organizer's upgrade is a request the owner approves."""
from django.conf import settings
from django.utils import timezone

FREE, STANDARD, STUDIO = 'free', 'standard', 'studio'
LABELS = {FREE: 'Free', STANDARD: 'Standard', STUDIO: 'Studio'}


def prices():
    from platform_admin.models import PlatformSettings
    s = PlatformSettings.load()
    return {STANDARD: s.standard_price_usd, STUDIO: s.studio_price_usd}


def price_label(kind):
    value = prices()[kind]
    return f'${value:,.0f}' if value == int(value) else f'${value:,.2f}'


def plan_of(event):
    if event.plan == event.PLAN_STUDIO:
        return STUDIO
    return STANDARD if event.paid else FREE


def can_go_live(event):
    """May the event's Studio website be public? The Studio plan, or the owner's override."""
    if event.plan == event.PLAN_STUDIO:
        return True
    from platform_admin.models import EventOverride
    override = EventOverride.of(event)
    return bool(override and override.allow_publishing)


def request_upgrade(event, user, kind, message=''):
    """An upgrade request (one pending per event and kind) + an email to the admins. Returns (request, created)."""
    from django.core.mail import send_mail
    from django.urls import reverse

    from platform_admin.models import UpgradeRequest
    pending = UpgradeRequest.objects.filter(event=event, kind=kind, status=UpgradeRequest.PENDING).first()
    if pending is not None:
        return pending, False
    req = UpgradeRequest.objects.create(event=event, requested_by=user, kind=kind, price_usd=prices()[kind],
                                        message=(message or '')[:2000])
    admins = [email for _, email in getattr(settings, 'ADMINS', [])]
    if admins:
        body = (f'{getattr(user, "email", "Someone")} asks for {LABELS[kind]} ({price_label(kind)}) on “{event.name}” '
                f'({event.event_code}, id {event.pk}).\n\n{message or "(no note)"}\n\n'
                f'Decide in the Platform admin: {settings.SITE_URL}{reverse("platform:home")}?tab=requests')
        send_mail(f'Upgrade request: {event.name} → {LABELS[kind]}', body, None, admins, fail_silently=True)
    return req, True


def approve(req, user):
    """Sets the plan and paid, records the payment at the request's price."""
    from events.models import Event
    from platform_admin.models import PaymentRecord, UpgradeRequest
    event = req.event
    event.paid = True
    if req.kind == UpgradeRequest.STUDIO:
        event.plan = Event.PLAN_STUDIO
    event.save(update_fields=['paid', 'plan'])
    PaymentRecord.objects.create(event=event, plan=req.kind, amount_usd=req.price_usd, recorded_by=user,
                                 note=f'Approved upgrade request {req.pk}')
    req.status, req.decided_by, req.decided_at = UpgradeRequest.APPROVED, user, timezone.now()
    req.save(update_fields=['status', 'decided_by', 'decided_at'])
    return req


def decline(req, user):
    from platform_admin.models import UpgradeRequest
    req.status, req.decided_by, req.decided_at = UpgradeRequest.DECLINED, user, timezone.now()
    req.save(update_fields=['status', 'decided_by', 'decided_at'])
    return req
