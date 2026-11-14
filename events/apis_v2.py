"""
API v2 — one request loads everything the app shows.

    GET  /api/v2/events/nearby/?lat=&lng=
    GET  /api/v2/events/lookup/?q=<code or exact name>
    GET  /api/v2/events/<id>/bundle/          (ETag / If-None-Match → 304)
    POST /api/v2/events/<id>/join/            {"device_id", "platform", "push_token"?, "app_version"?}
    POST /api/v2/events/<id>/leave/           {"device_id"}
    POST /api/v2/devices/                     {"device_id", "platform", "push_token"?, "app_version"?, "subscribed"?}

Authentication is identical to v1 (static key or attestation JWT).
"""

import decimal
import json

from django.http import HttpResponse, JsonResponse
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from events import content
from events.apis import ApiKeyRequired, live_events, lookup_event, nearby_events, register_device
from events.models import Device

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
    def get(self, request):
        try:
            lat = decimal.Decimal(request.GET['lat'])
            lng = decimal.Decimal(request.GET['lng'])
        except (KeyError, decimal.InvalidOperation):
            return JsonResponse({'error': 'lat and lng are required numbers'}, status=400)
        results = [
            {'event': content.event_v2(e, request), 'distance_miles': miles, 'starts_in_hours': hours}
            for e, miles, hours in nearby_events(lat, lng)
        ]
        return JsonResponse({'results': results, 'count': len(results)})


@method_decorator(csrf_exempt, name='dispatch')
class LookupV2(ApiKeyRequired, View):
    def get(self, request):
        q = request.GET.get('q', '').strip()
        if not q:
            return JsonResponse({'error': 'Missing q parameter'}, status=400)
        event = lookup_event(q)
        if event is None:
            return JsonResponse({'error': 'Not found'}, status=404)
        return JsonResponse({'event': content.event_v2(event, request)})


@method_decorator(csrf_exempt, name='dispatch')
class BundleV2(ApiKeyRequired, View):
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
        if device_id:
            Device.objects.filter(id=device_id[:128]).update(last_seen=timezone.now(), api_version=API_VERSION)
        return resp


@method_decorator(csrf_exempt, name='dispatch')
class JoinV2(ApiKeyRequired, View):
    def post(self, request, event_id):
        event = live_events().filter(id=event_id).first()
        if event is None:
            return JsonResponse({'error': 'Not found'}, status=404)
        body = _body(request)
        device_id = body.pop('device_id', '')
        if not device_id:
            return JsonResponse({'error': 'device_id is required'}, status=400)
        body.pop('event', None)
        body.pop('api_version', None)
        register_device(device_id, event=event, api_version=API_VERSION, **body)
        return JsonResponse({'status': 'ok', 'content_version': event.content_version})


@method_decorator(csrf_exempt, name='dispatch')
class LeaveV2(ApiKeyRequired, View):
    def post(self, request, event_id):
        device_id = _body(request).get('device_id', '')
        if not device_id:
            return JsonResponse({'error': 'device_id is required'}, status=400)
        Device.objects.filter(id=device_id, event_id=event_id).update(event=None)
        return JsonResponse({'status': 'ok'})


@method_decorator(csrf_exempt, name='dispatch')
class DeviceV2(ApiKeyRequired, View):
    def post(self, request):
        body = _body(request)
        device_id = body.pop('device_id', '')
        if not device_id:
            return JsonResponse({'error': 'device_id is required'}, status=400)
        body.pop('event', None)
        body.pop('api_version', None)
        device = register_device(device_id, api_version=API_VERSION, **body)
        return JsonResponse({'status': 'ok', 'subscribed': device.subscribed,
                             'event_id': device.event_id})
