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


PLAN_TONES = {FREE: 'muted', STANDARD: 'neutral', STUDIO: 'info'}
PLAN_TITLES = {FREE: 'Free: an unpaid demo, limited phones', STANDARD: 'Standard: paid, no phone limit, nearby listing',
               STUDIO: 'Studio: Standard + publishing the event website'}


def override_details(event):
    """What the Platform admin changed for this event (EventOverride) and for its owner's AI credit, as short lines;
    [] when nothing applies (a row with only notes or defaults doesn't count)."""
    from platform_admin.models import EventOverride
    out = []
    o = EventOverride.of(event)
    if o is not None:
        if o.no_free_limit:
            out.append('No free phone limit')
        elif o.free_device_limit is not None:
            out.append(f'Free phones: {o.free_device_limit}')
        if o.allow_publishing:
            out.append('Website publishing allowed without Studio')
        if o.studio_daily_cap_usd is not None:
            out.append(f'Studio AI per day: ${o.studio_daily_cap_usd:,.2f}')
    if event.user_id:
        from credit.models import AiLimitOverride
        ai = AiLimitOverride.objects.filter(user_id=event.user_id).first()
        if ai is not None and (ai.unlimited or ai.monthly_usd is not None):
            out.append("Owner's AI credit: " + ('no limit' if ai.unlimited else f'${ai.monthly_usd:,.0f} a month'))
    return out


def header_badges(event, user):
    """The event header's plan badge (Free / Standard / Studio) and, when an override applies, an "Override" badge:
    staff with the Platform page see what it changes (title + a link for platform admins), others only that one
    applies."""
    from django.urls import reverse
    plan = plan_of(event)
    out = [{'label': LABELS[plan], 'tone': PLAN_TONES[plan], 'title': PLAN_TITLES[plan], 'kind': 'plan'}]
    details = override_details(event)
    if details:
        staff = user.has_perm('platform_admin.view_platformsettings')
        out.append({'label': 'Override', 'tone': 'warning', 'kind': 'override',
                    'title': ' · '.join(details) if staff else 'Attnd changed a limit for this event',
                    'details': details if staff else [],
                    'url': reverse('platform:event', args=[event.pk])
                    if user.has_perm('platform_admin.change_platformsettings') else ''})
    return out


def can_go_live(event):
    """May the event's Studio website be public? The Studio plan, or the owner's override."""
    if event.plan == event.PLAN_STUDIO:
        return True
    from platform_admin.models import EventOverride
    override = EventOverride.of(event)
    return bool(override and override.allow_publishing)


def request_upgrade(event, user, kind, message=''):
    """An upgrade request (one pending per event and kind) + an email to the admins. Returns (request, created)."""
    from django.urls import reverse

    from console import mail

    from platform_admin.models import UpgradeRequest
    pending = UpgradeRequest.objects.filter(event=event, kind=kind, status=UpgradeRequest.PENDING).first()
    if pending is not None:
        return pending, False
    req = UpgradeRequest.objects.create(event=event, requested_by=user, kind=kind, price_usd=prices()[kind],
                                        message=(message or '')[:2000])
    admins = [email for _, email in getattr(settings, 'ADMINS', [])]
    if admins:
        mail.send('upgrade_request', admins, f'Upgrade request: {event.name} → {LABELS[kind]}',
                  paragraphs=[f'{getattr(user, "email", "Someone")} asks for {LABELS[kind]} ({price_label(kind)}) on '
                              f'“{event.name}” ({event.event_code}, id {event.pk}).', message or '(no note)'],
                  button={'label': 'Decide in the Platform admin',
                          'url': f'{settings.SITE_URL}{reverse("platform:home")}?tab=requests'})
    return req, True


def approve(req, user):
    """Sets the plan and paid, records the payment at the request's price (as a paid invoice, batch 7 billing)."""
    from platform_admin import billing
    from platform_admin.models import UpgradeRequest
    billing.record_manual(req.event, req.kind, req.price_usd, user, note=f'Approved upgrade request {req.pk}')
    req.status, req.decided_by, req.decided_at = UpgradeRequest.APPROVED, user, timezone.now()
    req.save(update_fields=['status', 'decided_by', 'decided_at'])
    return req


def decline(req, user):
    from platform_admin.models import UpgradeRequest
    req.status, req.decided_by, req.decided_at = UpgradeRequest.DECLINED, user, timezone.now()
    req.save(update_fields=['status', 'decided_by', 'decided_at'])
    return req
