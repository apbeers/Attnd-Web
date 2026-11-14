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
    return f'"e{event.id}-v{event.content_version}"'


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


@method_decorator(csrf_exempt, name='dispatch')
class BundleV2(ApiKeyRequired, View):
    ci_scope = 'event'

    def get(self, request, event_id):
        event = live_events().filter(id=event_id).first()
        if event is None:
            return JsonResponse({'error': 'Not found'}, status=404)
        etag = _etag(event)
        if request.headers.get('If-None-Match') == etag:
            resp = HttpResponse(status=304)
        else:
            resp = JsonResponse(content.bundle(event, request))
        resp['ETag'] = etag
        resp['Cache-Control'] = 'no-cache'
        device_id = request.headers.get('X-Device-Id')
        if device_id and (is_ci_device(device_id) or not is_ci_request(request)):
            Device.objects.filter(id=device_id[:128]).update(last_seen=timezone.now(), api_version=API_VERSION)
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
