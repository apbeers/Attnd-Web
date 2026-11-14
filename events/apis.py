import decimal
import json
from datetime import timedelta
from difflib import get_close_matches
from math import sqrt

from django.conf import settings
from django.http import JsonResponse
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from django.views import View

from events.models import Event, Location, Notification, Device, ScheduledItem
from attestation import tokens as attest_tokens


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------

def _coord_or(value, fallback):
    if value is not None:
        return float(value)
    return float(fallback) if fallback is not None else 0.0


def live_events():
    """Events attendees can reach (drafts are hidden from every API)."""
    return Event.objects.exclude(status=Event.STATUS_DRAFT)


def _event_dict(event, request=None):
    if event.flyer:
        flyer_url = request.build_absolute_uri(event.flyer.url) if request else event.flyer.url
    else:
        flyer_url = None
    return {
        'id': event.id,
        'name': event.name,
        'event_code': event.event_code,
        'description': event.description,
        'latitude': _coord_or(event.latitude, None),
        'longitude': _coord_or(event.longitude, None),
        'start_time': event.start_time.isoformat(),
        'created_at': event.created_at.isoformat(),
        'paid': event.paid,
        'flyer_url': flyer_url,
        'address': event.address,
    }


def _scheduled_item_dict(item):
    return {
        'id': item.id,
        'name': item.name,
        'description': item.description,
        'start_time': item.start_time.isoformat(),
        'location_id': item.location_id,
        'location_name': item.location.name if item.location_id else '',
    }


def _location_dict(location, request=None):
    if location.image:
        image_url = request.build_absolute_uri(location.image.url) if request else location.image.url
    elif location.image_url:
        image_url = location.image_url
    else:
        image_url = None
    scheduled_items = [
        _scheduled_item_dict(item)
        for item in location.scheduleditem_set.filter(hidden=False).order_by('start_time')
    ]
    event = location.event
    return {
        'id': location.id,
        'name': location.name,
        'snippet': location.snippet,
        'description': location.description,
        'website': location.website,
        'image_url': image_url,
        # v1 clients require numbers; fall back to the event's pin when unknown
        'latitude': _coord_or(location.latitude, event.latitude),
        'longitude': _coord_or(location.longitude, event.longitude),
        'address': location.address,
        'scheduled_items': scheduled_items,
    }


def _notification_dict(notification):
    return {
        'id': notification.id,
        'title': notification.title,
        'body': notification.body,
        'send_time': notification.send_time.isoformat(),
        'sent': notification.sent,
    }


def haversine_miles(lat1, lon1, lat2, lon2):
    from math import asin, cos, radians, sin
    lat1, lon1, lat2, lon2 = map(radians, (float(lat1), float(lon1), float(lat2), float(lon2)))
    h = sin((lat2 - lat1) / 2) ** 2 + cos(lat1) * cos(lat2) * sin((lon2 - lon1) / 2) ** 2
    return 2 * 3958.8 * asin(sqrt(h))


def nearby_events(latitude, longitude, now=None):
    """Listed, published events near a point, sorted by relevance. Returns [(event, miles, hours)]."""
    now = now or timezone.now()
    radius = decimal.Decimal('0.5')
    events = Event.objects.filter(
        public=True,
        status=Event.STATUS_PUBLISHED,
        latitude__isnull=False,
        longitude__isnull=False,
        latitude__lte=latitude + radius,
        latitude__gte=latitude - radius,
        longitude__lte=longitude + radius,
        longitude__gte=longitude - radius,
        start_time__lte=now + timedelta(days=7),
        start_time__gte=now - timedelta(days=3),
    )
    scored = []
    for event in events:
        miles = round(haversine_miles(latitude, longitude, event.latitude, event.longitude), 1)
        hours = round((event.start_time - now).total_seconds() / 3600, 1)
        scored.append((_relevance_score(miles, event.start_time, now), event, miles, hours))
    scored.sort(key=lambda x: x[0])
    return [(e, m, h) for _, e, m, h in scored]


def _relevance_score(distance_miles, start_time, now):
    hours_until = (start_time - now).total_seconds() / 3600
    time_score = abs(hours_until) / 24
    distance_score = distance_miles / 10
    return time_score * 0.6 + distance_score * 0.4


# ---------------------------------------------------------------------------
# Auth mixin
# ---------------------------------------------------------------------------

# SHA-256 of static API keys that were exposed (e.g. committed to a repo). Never accepted,
# even if the API_KEY environment variable still holds one of them.
REVOKED_API_KEY_SHA256 = {
    '350fcb758264f3634f5a65c7278014dc34d08fd592cac9cefb722f8937af61b0',
}


def static_key_ok(bearer):
    import hashlib
    import hmac
    key = settings.API_KEY
    if not key or not bearer or not getattr(settings, 'STATIC_API_KEY_ENABLED', True):
        return False
    if hashlib.sha256(key.encode()).hexdigest() in REVOKED_API_KEY_SHA256:
        return False
    return hmac.compare_digest(bearer.encode(), key.encode())


class ApiKeyRequired:
    """
    Accept either:
      - A static API key (Authorization: Bearer <API_KEY>) for backward compat
        with CI, dev environments, and the existing console.
      - A short-lived attestation JWT issued by /api/v1/attest/ios/ or
        /api/v1/attest/android/ for production mobile clients.
    """

    def dispatch(self, request, *args, **kwargs):
        bearer = request.headers.get('Authorization', '').removeprefix('Bearer ').strip()
        if not bearer:
            return JsonResponse({'error': 'Unauthorized'}, status=401)

        # Static API key check (backward compat)
        if static_key_ok(bearer):
            return super().dispatch(request, *args, **kwargs)

        # Attestation JWT check
        payload = attest_tokens.verify(bearer)
        if payload is not None:
            request.attested_device_key = payload.get('sub')
            request.attested_platform = payload.get('platform')
            return super().dispatch(request, *args, **kwargs)

        return JsonResponse({'error': 'Unauthorized'}, status=401)


# ---------------------------------------------------------------------------
# Event endpoints
# ---------------------------------------------------------------------------

@method_decorator(csrf_exempt, name='dispatch')
class SingleEventView(ApiKeyRequired, View):

    def get(self, request, event_id):
        event = live_events().filter(id=event_id).first()
        if event is None:
            return JsonResponse({'error': 'Not found'}, status=404)
        return JsonResponse(_event_dict(event, request))


@method_decorator(csrf_exempt, name='dispatch')
class NearbyEventsView(ApiKeyRequired, View):

    def get(self, request, latitude, longitude):
        try:
            latitude = decimal.Decimal(latitude)
            longitude = decimal.Decimal(longitude)
        except Exception:
            return JsonResponse({'error': 'Invalid coordinates'}, status=400)

        results = [
            {'event': _event_dict(event, request), 'distance_miles': miles, 'starts_in_hours': hours}
            for event, miles, hours in nearby_events(latitude, longitude)
        ]
        return JsonResponse({'results': results, 'count': len(results)})


@method_decorator(csrf_exempt, name='dispatch')
class EventByCodeOrNameView(ApiKeyRequired, View):

    def get(self, request):
        name_or_code = request.GET.get('data', '')
        candidates = []
        listed = Event.objects.filter(public=True, status=Event.STATUS_PUBLISHED)
        for name, code in listed.values_list('name', 'event_code'):
            candidates.append(name)
            candidates.append(code)

        matches = get_close_matches(word=name_or_code, possibilities=candidates, n=1)
        if not matches:
            return JsonResponse({'error': 'Not found'}, status=404)

        match = matches[0]
        event = listed.filter(name=match).first() or listed.filter(event_code=match).first()
        if event is None:
            return JsonResponse({'error': 'Not found'}, status=404)
        return JsonResponse(_event_dict(event, request))


def lookup_event(q):
    """Exact (case-insensitive) match by event code, then by name. Ignores the public flag."""
    return (
        live_events().filter(event_code__iexact=q).first()
        or live_events().filter(name__iexact=q).order_by('-start_time').first()
    )


@method_decorator(csrf_exempt, name='dispatch')
class EventLookupView(ApiKeyRequired, View):
    """Exact match by name or event_code. Does not filter by public — for organizer use."""

    def get(self, request):
        q = request.GET.get('q', '').strip()
        if not q:
            return JsonResponse({'error': 'Missing q parameter'}, status=400)
        event = lookup_event(q)
        if event is None:
            return JsonResponse({'error': 'Not found'}, status=404)
        return JsonResponse(_event_dict(event, request))


# ---------------------------------------------------------------------------
# Location endpoints
# ---------------------------------------------------------------------------

@method_decorator(csrf_exempt, name='dispatch')
class SingleLocationView(ApiKeyRequired, View):

    def get(self, request, event_id, location_id):
        location = Location.objects.filter(event__id=event_id, id=location_id, hidden=False).first()
        if location is None:
            return JsonResponse({'error': 'Not found'}, status=404)
        return JsonResponse(_location_dict(location, request))


@method_decorator(csrf_exempt, name='dispatch')
class AllLocationsView(ApiKeyRequired, View):

    def get(self, request, event_id):
        locations = Location.objects.filter(event__id=event_id, hidden=False).select_related('event')
        results = [_location_dict(loc, request) for loc in locations]
        return JsonResponse({'results': results, 'count': len(results)})


# ---------------------------------------------------------------------------
# Schedule endpoints
# ---------------------------------------------------------------------------

@method_decorator(csrf_exempt, name='dispatch')
class AllScheduledItemsView(ApiKeyRequired, View):

    def get(self, request, event_id):
        items = (
            ScheduledItem.objects
            .filter(event_id=event_id, hidden=False, location__isnull=False, location__hidden=False)
            .select_related('location')
            .order_by('start_time')
        )
        results = [_scheduled_item_dict(item) for item in items]
        return JsonResponse({'results': results, 'count': len(results)})


@method_decorator(csrf_exempt, name='dispatch')
class SingleScheduledItemView(ApiKeyRequired, View):

    def get(self, request, event_id, item_id):
        item = ScheduledItem.objects.filter(event_id=event_id, id=item_id).select_related('location').first()
        if item is None:
            return JsonResponse({'error': 'Not found'}, status=404)
        return JsonResponse(_scheduled_item_dict(item))


# ---------------------------------------------------------------------------
# Notification endpoints
# ---------------------------------------------------------------------------

@method_decorator(csrf_exempt, name='dispatch')
class SingleNotificationView(ApiKeyRequired, View):

    def get(self, request, event_id, notification_id):
        notification = Notification.objects.filter(event__id=event_id, id=notification_id).first()
        if notification is None:
            return JsonResponse({'error': 'Not found'}, status=404)
        return JsonResponse(_notification_dict(notification))


@method_decorator(csrf_exempt, name='dispatch')
class AllNotificationsView(ApiKeyRequired, View):

    def get(self, request, event_id):
        notifications = Notification.objects.filter(sent=True, event__id=event_id).order_by('-send_time')
        results = [_notification_dict(n) for n in notifications]
        return JsonResponse({'results': results, 'count': len(results)})


# ---------------------------------------------------------------------------
# Device / membership endpoints
# ---------------------------------------------------------------------------

def register_device(device_id, event=..., platform='', push_token=None, app_version=None,
                    subscribed=None, api_version=None, **_ignored):
    """Create or update a device. ``event=...`` leaves the joined event unchanged."""
    device_id = str(device_id)[:128]
    device, created = Device.objects.get_or_create(id=device_id, defaults={'platform': platform or ''})
    now = timezone.now()
    newly_joined = None
    if event is not ...:
        if event is not None and device.event_id != event.id:
            device.joined_at = now
            newly_joined = event
        device.event = event
    if platform in ('ios', 'android'):
        device.platform = platform
    if isinstance(push_token, str):
        device.push_token = push_token[:512]
    if isinstance(app_version, str):
        device.app_version = app_version[:32]
    if isinstance(subscribed, bool):
        device.subscribed = subscribed
    if api_version:
        device.api_version = api_version
    device.last_seen = now
    device.save()
    # Anonymous usage counters (API-005): totals only, never which device.
    from events import usage
    if created:
        usage.record(None, 'install', platform=device.platform)
    if newly_joined is not None:
        usage.record(newly_joined, 'join', platform=device.platform)
    return device


@method_decorator(csrf_exempt, name='dispatch')
class JoinEventView(ApiKeyRequired, View):

    def post(self, request, event_id, device_id):
        event = live_events().filter(id=event_id).first()
        if event is None:
            return JsonResponse({'error': 'Event not found'}, status=404)
        try:
            body = json.loads(request.body or b'{}')
            if not isinstance(body, dict):
                body = {}
        except (json.JSONDecodeError, AttributeError):
            body = {}
        body = {k: v for k, v in body.items() if k not in ('event', 'api_version')}
        register_device(device_id, event=event, api_version=1, **body)
        return JsonResponse({'status': 'ok'})


@method_decorator(csrf_exempt, name='dispatch')
class LeaveEventView(ApiKeyRequired, View):

    def post(self, request, device_id, event_id):
        _leave(device_id, event_id=None)  # v1 always cleared the device's event, whatever the id
        return JsonResponse({'status': 'ok'})


def _leave(device_id, event_id):
    """Detach a device from its event (only from `event_id` when given) and count the leave anonymously."""
    devices = Device.objects.filter(id=device_id).exclude(event=None).select_related('event')
    if event_id is not None:
        devices = devices.filter(event_id=event_id)
    device = devices.first()
    if device is None:
        return
    Device.objects.filter(pk=device.pk).update(event=None)
    from events import usage
    usage.record(device.event, 'leave', platform=device.platform)


@method_decorator(csrf_exempt, name='dispatch')
class PushNotificationsSubscribe(ApiKeyRequired, View):

    def post(self, request, device_id):
        Device.objects.filter(id=device_id).update(subscribed=True, api_version=1, last_seen=timezone.now())
        return JsonResponse({'status': 'ok'})


@method_decorator(csrf_exempt, name='dispatch')
class PushNotificationsUnSubscribe(ApiKeyRequired, View):

    def post(self, request, device_id):
        Device.objects.filter(id=device_id).update(subscribed=False, api_version=1, last_seen=timezone.now())
        return JsonResponse({'status': 'ok'})
