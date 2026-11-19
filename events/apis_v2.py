"""
API v2 — one request loads everything the app shows.

    GET  /api/v2/events/nearby/?lat=&lng=
    GET  /api/v2/events/lookup/?q=<code or exact name>
    GET  /api/v2/events/<id>/bundle/          (ETag / If-None-Match → 304)
    POST /api/v2/events/<id>/join/            {"device_id", "platform", "push_token"?, "app_version"?}
    POST /api/v2/events/<id>/leave/           {"device_id"}
    POST /api/v2/devices/                     {"device_id", "platform", "push_token"?, "app_version"?, "subscribed"?}

Authentication is identical to v1 (static key or attestation JWT), plus CI keys limited to the DEMO
event (API-008, see ApiKeyRequired).
"""

import decimal
import json
from datetime import timedelta

from django.core.cache import cache
from django.core.serializers.json import DjangoJSONEncoder
from django.db.models import Q
from django.http import HttpResponse, JsonResponse
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from events import content
from events import usage
from events.apis import (ApiKeyRequired, _leave, _not_found, ci_device_refused, demo_event, is_ci_request, live_events,
                         lookup_event, nearby_events, register_device)
from events.models import Device, is_ci_device

API_VERSION = 2


def _body(request):
    try:
        data = json.loads(request.body or b'{}')
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {}


def _etag(event):
    return _etag_for(event.id, event.content_version)


def _etag_for(event_id, content_version):
    return f'"e{event_id}-v{content_version}"'


@method_decorator(csrf_exempt, name='dispatch')
class NearbyV2(ApiKeyRequired, View):
    ci_scope = 'nearby'

    def get(self, request):
        try:
            lat = decimal.Decimal(request.GET['lat'])
            lng = decimal.Decimal(request.GET['lng'])
        except (KeyError, decimal.InvalidOperation):
            return JsonResponse({'error': 'lat and lng are required numbers'}, status=400)
        found = nearby_events(lat, lng)
        if is_ci_request(request):
            demo = demo_event()
            found = [r for r in found if demo is not None and r[0].id == demo.id]
        results = [
            {'event': content.event_v2(e, request), 'distance_miles': miles, 'starts_in_hours': hours}
            for e, miles, hours in found
        ]
        return JsonResponse({'results': results, 'count': len(results)})


@method_decorator(csrf_exempt, name='dispatch')
class LookupV2(ApiKeyRequired, View):
    ci_scope = 'lookup'

    def get(self, request):
        q = request.GET.get('q', '').strip()
        if not q:
            return JsonResponse({'error': 'Missing q parameter'}, status=400)
        event = lookup_event(q)
        if event is None:
            return _not_found()
        if is_ci_request(request):
            demo = demo_event()
            if demo is None or event.id != demo.id:
                return _not_found()  # same answer as an unknown code: a CI key can't probe for events
        return JsonResponse({'event': content.event_v2(event, request)})


# The serialized bundle per (event, content_version, origin) for a few minutes: every device of an event
# fetches the same bytes after a change (the content_updated push), so only the first one builds them. The
# version is in the key, so an edit is never served stale; the TTL only bounds what can change without a
# version bump (the ticket sales window opening or closing on its own, which the ETag ignores too).
BUNDLE_CACHE_SECONDS = 300
# A device's last_seen is written at most this often by bundle polls (the apps poll every 60 s – 5 min;
# last_seen only feeds "active today" counts and the one-year clean-up).
LAST_SEEN_RESOLUTION = timedelta(minutes=10)


def _bundle_bytes(event, request):
    key = f'bundle:v2:{event.id}:{event.content_version}:{request.scheme}://{request.get_host()}'
    body = cache.get(key)
    if body is None:
        body = json.dumps(content.bundle(event, request), cls=DjangoJSONEncoder).encode()
        cache.set(key, body, BUNDLE_CACHE_SECONDS)
    return body


def _touch_device(request):
    device_id = request.headers.get('X-Device-Id')
    if not device_id or (is_ci_request(request) and not is_ci_device(device_id)):
        return
    now = timezone.now()
    # Only rows that need it: a poll every minute would otherwise rewrite the device row every minute.
    (Device.objects.filter(id=device_id[:128])
     .filter(Q(last_seen__isnull=True) | Q(last_seen__lt=now - LAST_SEEN_RESOLUTION) | ~Q(api_version=API_VERSION))
     .update(last_seen=now, api_version=API_VERSION))


@method_decorator(csrf_exempt, name='dispatch')
class BundleV2(ApiKeyRequired, View):
    ci_scope = 'event'

    def get(self, request, event_id):
        events = live_events().filter(id=event_id)
        etag = None
        if_none_match = request.headers.get('If-None-Match')
        if if_none_match:
            # The usual poll: answer 304 from the version alone, without loading the event or its content.
            version = events.values_list('content_version', flat=True).first()
            if version is None:
                return JsonResponse({'error': 'Not found'}, status=404)
            etag = _etag_for(event_id, version)
        if etag is not None and if_none_match == etag:
            resp = HttpResponse(status=304)
        else:
            event = events.first()
            if event is None:
                return JsonResponse({'error': 'Not found'}, status=404)
            etag = _etag(event)
            resp = HttpResponse(_bundle_bytes(event, request), content_type='application/json')
        resp['ETag'] = etag
        resp['Cache-Control'] = 'no-cache'
        _touch_device(request)
        return resp


@method_decorator(csrf_exempt, name='dispatch')
class JoinV2(ApiKeyRequired, View):
    ci_scope = 'event'

    def post(self, request, event_id):
        event = live_events().filter(id=event_id).first()
        if event is None:
            return JsonResponse({'error': 'Not found'}, status=404)
        body = _body(request)
        device_id = body.pop('device_id', '')
        if not device_id:
            return JsonResponse({'error': 'device_id is required'}, status=400)
        if refused := ci_device_refused(request, device_id):
            return refused
        body.pop('event', None)
        body.pop('api_version', None)
        register_device(device_id, event=event, api_version=API_VERSION, **body)
        return JsonResponse({'status': 'ok', 'content_version': event.content_version})


@method_decorator(csrf_exempt, name='dispatch')
class LeaveV2(ApiKeyRequired, View):
    ci_scope = 'event'

    def post(self, request, event_id):
        device_id = _body(request).get('device_id', '')
        if not device_id:
            return JsonResponse({'error': 'device_id is required'}, status=400)
        if refused := ci_device_refused(request, device_id):
            return refused
        _leave(device_id, event_id)
        return JsonResponse({'status': 'ok'})


@method_decorator(csrf_exempt, name='dispatch')
class DeviceV2(ApiKeyRequired, View):
    ci_scope = 'device'

    def post(self, request):
        body = _body(request)
        device_id = body.pop('device_id', '')
        if not device_id:
            return JsonResponse({'error': 'device_id is required'}, status=400)
        if refused := ci_device_refused(request, device_id):
            return refused
        body.pop('event', None)
        body.pop('api_version', None)
        device = register_device(device_id, api_version=API_VERSION, **body)
        return JsonResponse({'status': 'ok', 'subscribed': device.subscribed,
                             'event_id': device.event_id})


@method_decorator(csrf_exempt, name='dispatch')
class UsageV2(ApiKeyRequired, View):
    ci_scope = 'event'

    """POST events/<id>/usage/  {"platform": "ios"|"android", "events": [{"name", "detail"?, "count"?, "at"?}]}

    Anonymous usage counts (API-005). No device ID is accepted or stored. Invalid entries are
    dropped silently, so analytics never makes an app request fail.
    """

    def post(self, request, event_id):
        event = live_events().filter(id=event_id).first()
        if event is None:
            return JsonResponse({'error': 'Not found'}, status=404)
        body = _body(request)
        platform = body.get('platform') if body.get('platform') in ('ios', 'android') else ''
        # A CI key gets exactly the answer a real app would, but nothing is recorded (API-008).
        accepted = usage.ingest(event, platform, body.get('events'), save=not is_ci_request(request))
        return JsonResponse({'status': 'ok', 'accepted': accepted})
