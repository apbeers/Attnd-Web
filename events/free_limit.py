"""API-025: the free limit. An unpaid (demo) event takes at most ``FREE_DEVICE_LIMIT`` different real devices.

- Counted on join (``EventJoin``: each device once, ever). A device that joined before always rejoins.
- The next new device gets ``409`` ``{"error": "event_full_free", "message": …, "limit": 20}``.
- Exempt: paid events, showcase events, the DEMO event and the App Review sample events (``seed_test_data``).
- Not counted: CI devices (``ci-…``; CI keys only reach DEMO anyway) and the showcase-test devices (``showcase-…``).

The console shows "14 of 20 free spots used"; "Remove the limit" emails the admins, and an admin marks the event paid.
"""

from django.conf import settings
from django.db import IntegrityError, transaction

FREE_DEVICE_LIMIT = 20
ERROR = 'event_full_free'
MESSAGE = ('This event is full for now: its free trial takes {limit} people. Ask the organizer to remove the limit, '
           'then try again.')
# Device ids that never count: automated tests (API-008) and the showcase checks.
UNCOUNTED_PREFIXES = ('ci-', 'showcase-')
# seed_test_data's App Review sample events.
REVIEW_CODES = {'FWFM2026', 'ATIS2026', 'DACN2026'}


def limit(event=None):
    """Free phones of an unpaid event: the owner's per-event override, else the Platform admin's setting
    (PlatformSettings; its first value FREE_DEVICE_LIMIT)."""
    if event is not None:
        from platform_admin.models import EventOverride
        override = EventOverride.of(event)
        if override is not None and override.free_device_limit is not None:
            return int(override.free_device_limit)
    from platform_admin.models import PlatformSettings
    return int(PlatformSettings.load().free_device_limit)


def exempt(event):
    from platform_admin.models import EventOverride
    override = EventOverride.of(event)
    if override is not None and override.no_free_limit:
        return True
    code = (event.event_code or '').upper()
    return bool(event.paid or event.showcase or code in REVIEW_CODES
                or code == str(getattr(settings, 'DEMO_EVENT_CODE', 'DEMO') or '').upper())


def counted(device_id):
    return not str(device_id).startswith(UNCOUNTED_PREFIXES)


def used(event):
    from events.models import EventJoin
    qs = EventJoin.objects.filter(event=event)
    for prefix in UNCOUNTED_PREFIXES:
        qs = qs.exclude(device_id__startswith=prefix)
    return qs.count()


def status(event):
    """For the console: {limited, used, limit, left, full}."""
    if exempt(event):
        return {'limited': False}
    n, cap = used(event), limit(event)
    return {'limited': True, 'used': n, 'limit': cap, 'left': max(cap - n, 0), 'full': n >= cap}


def admit(event, device_id):
    """Record the join; False when an unpaid event is full and this device never joined it."""
    from events.models import EventJoin
    device_id = str(device_id)[:128]
    if EventJoin.objects.filter(event=event, device_id=device_id).exists():
        return True
    if counted(device_id) and not exempt(event) and used(event) >= limit(event):
        return False
    try:
        with transaction.atomic():
            EventJoin.objects.create(event=event, device_id=device_id)
    except IntegrityError:
        pass  # joined twice at once
    return True


def refusal(event=None):
    n = limit(event)
    return {'error': ERROR, 'message': MESSAGE.format(limit=n), 'limit': n}
