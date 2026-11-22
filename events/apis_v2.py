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

from django.core.cache import caches

cache = caches['local']  # per process: rebuilt from content_version, no database round trip
from django.core.serializers.json import DjangoJSONEncoder
from django.db.models import Case, F, PositiveIntegerField, Q, Value, When
from django.http import HttpResponse, JsonResponse
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from events import content
from events import layout as screen_layout
from events import usage
from events.apis import (ApiKeyRequired, _leave, _not_found, ci_device_refused, demo_event, is_ci_request, live_events,
                         lookup_event, nearby_events, register_device)
from events.models import Device, Event, is_ci_device

API_VERSION = 2


def _body(request):
    try:
        data = json.loads(request.body or b'{}')
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {}


def _etag(event, sections=0):
    return _etag_for(event.id, event.content_version, sections)


def _etag_for(event_id, content_version, sections=0, audience=''):
    # API-024: apps that send X-Attnd-Sections get a body with the layout for that catalogue version, so the
    # version is part of the ETag (an app update with a newer catalogue never gets a 304 for the older body).
    # §4: the draft (preview phones) has ETags of its own (`d`); attendees keep the plain ones.
    suffix = f'-s{sections}' if sections else ''
    return f'"e{event_id}-v{content_version}{audience}{suffix}"'


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
# version is in the key, so an edit is never served stale; the TTL only bounds what could change without a
# version bump.
BUNDLE_CACHE_SECONDS = 300
# A device's last_seen is written at most this often by bundle polls (the apps poll every 60 s – 5 min;
# last_seen only feeds "active today" counts and the one-year clean-up).
LAST_SEEN_RESOLUTION = timedelta(minutes=10)


def _bundle_bytes(event, request, sections=0):
    key = f'bundle:v2:{event.id}:{event.content_version}:s{sections}:{request.scheme}://{request.get_host()}'
    body = cache.get(key)
    if body is None:
        body = json.dumps(content.bundle(event, request, sections), cls=DjangoJSONEncoder).encode()
        cache.set(key, body, BUNDLE_CACHE_SECONDS)
    return body


def _is_preview(request, event_id):
    device_id = (request.headers.get('X-Device-Id') or '')[:128]
    return bool(device_id) and Device.objects.previews().filter(id=device_id, event_id=event_id).exists()


def _published_bytes(event, pb, request, sections=0):
    key = f'bundle:pub:{event.id}:{pb.version}:{pb.published_at.timestamp():.0f}:s{sections}'
    body = cache.get(key)
    if body is None:
        from events import drafts
        body = json.dumps(drafts.attendee_bundle(event, pb, sections, request), cls=DjangoJSONEncoder).encode()
        cache.set(key, body, BUNDLE_CACHE_SECONDS)
    return body


def _touch_device(request, event_id=None, version=None):
    device_id = request.headers.get('X-Device-Id')
    if not device_id or (is_ci_request(request) and not is_ci_device(device_id)):
        return
    now = timezone.now()
    # Only rows that need it: a poll every minute would otherwise rewrite the device row every minute. A new content
    # version is written at once (API-026: "Updated on 31 of 37 phones"), once per device per change.
    needs = Q(last_seen__isnull=True) | Q(last_seen__lt=now - LAST_SEEN_RESOLUTION) | ~Q(api_version=API_VERSION)
    values = {'last_seen': now, 'api_version': API_VERSION}
    if version is not None:
        needs |= Q(event_id=event_id) & (Q(last_version__isnull=True) | Q(last_version__lt=version))
        # the version belongs to the event the device joined
        values['last_version'] = Case(When(event_id=event_id, then=Value(version)), default=F('last_version'),
                                       output_field=PositiveIntegerField())
    Device.objects.filter(id=device_id[:128]).filter(needs).update(**values)


@method_decorator(csrf_exempt, name='dispatch')
class BundleV2(ApiKeyRequired, View):
    ci_scope = 'event'

    def get(self, request, event_id):
        from events import drafts
        sections = screen_layout.sections_header(request)
        # §4: preview phones get the draft (the rows as they are now, also of a draft event; ETag `…d`); attendees the
        # published copy (PublishedBundle) when the event has one, else the rows (as before).
        preview = _is_preview(request, event_id)
        events = (Event.objects if preview else live_events()).filter(id=event_id)
        audience = 'd' if preview else ''
        etag = version = None
        if_none_match = request.headers.get('If-None-Match')
        if if_none_match:
            # The usual poll: answer 304 from the version alone, without loading the event or its content.
            version = None if preview else drafts.attendee_version(event_id)
            if version is None:
                version = events.values_list('content_version', flat=True).first()
            if version is None:
                return JsonResponse({'error': 'Not found'}, status=404)
            etag = _etag_for(event_id, version, sections, audience)
        if etag is not None and if_none_match == etag:
            resp = HttpResponse(status=304)
        else:
            event = (events if preview else events.select_related('published')).first()
            if event is None:
                return JsonResponse({'error': 'Not found'}, status=404)
            pb = None if preview else (getattr(event, 'published', None) or drafts.ensure(event))
            if pb is not None:
                version = pb.version
                resp = HttpResponse(_published_bytes(event, pb, request, sections), content_type='application/json')
            else:
                version = event.content_version
                resp = HttpResponse(_bundle_bytes(event, request, sections), content_type='application/json')
            etag = _etag_for(event_id, version, sections, audience)
        resp['ETag'] = etag
        resp['Cache-Control'] = 'no-cache'
        _touch_device(request, event_id, version)
        return resp


@method_decorator(csrf_exempt, name='dispatch')
class JoinV2(ApiKeyRequired, View):
    ci_scope = 'event'

    def post(self, request, event_id):
        body = _body(request)
        event = live_events().filter(id=event_id).first()
        if event is None:  # §4: a preview phone may join a draft event (its token says whose)
            from events import preview
            draft = Event.objects.filter(id=event_id, status=Event.STATUS_DRAFT).first()
            if draft is None or preview.read_token(body.get('preview_token'), draft) is None:
                return JsonResponse({'error': 'Not found'}, status=404)
            event = draft
        device_id = body.pop('device_id', '')
        if not device_id:
            return JsonResponse({'error': 'device_id is required'}, status=400)
        if refused := ci_device_refused(request, device_id):
            return refused
        body.pop('event', None)
        body.pop('api_version', None)
        # API-026: the organizer's preview phone (a token from the console's "Preview on my phone" QR). An invalid or
        # expired token just joins as an attendee.
        from events import preview
        preview_user = preview.read_token(body.pop('preview_token', None), event)
        over_cap = preview_user is not None and not preview.has_room(event, device_id)
        if over_cap:
            if event.status == Event.STATUS_DRAFT:  # nobody else may join a draft
                return JsonResponse({'error': 'Not found'}, status=404)
            preview_user = None  # batch 7: a 4th preview phone quietly joins as an attendee
        # API-025: an unpaid event takes FREE_DEVICE_LIMIT different devices; a device that joined before always can.
        # A preview phone never uses one of them.
        from events import free_limit
        if preview_user is None and not free_limit.admit(event, device_id):
            return JsonResponse(free_limit.refusal(event), status=409)
        device = register_device(device_id, event=event, api_version=API_VERSION, **body)
        answer = {'status': 'ok', 'content_version': event.content_version}
        if preview_user is not None:
            preview.mark(device, event, preview_user)
            answer['preview'] = True
        elif over_cap:
            from events.models import Device
            Device.objects.filter(pk=device.pk).update(preview_until=None, preview_by=None)  # not a preview any more
            answer['preview'] = False
        return JsonResponse(answer)


@method_decorator(csrf_exempt, name='dispatch')
class ChangesV2(ApiKeyRequired, View):
    """GET events/<id>/changes/?since=<content_version> (API-026): only for preview phones (X-Device-Id).

    Answers at once when the version is past `since`, else waits up to ~25 s; never holds a database connection
    while it waits. Not a preview phone, or a cap reached → at once, with `retry_after` (seconds)."""
    ci_scope = 'event'

    def get(self, request, event_id):
        from events import preview
        version = Event.objects.filter(id=event_id).values_list('content_version', flat=True).first()
        if version is None:
            return JsonResponse({'error': 'Not found'}, status=404)
        since = request.GET.get('since', '')
        since = int(since) if since.isdigit() else 0
        device_id = (request.headers.get('X-Device-Id') or '')[:128]
        is_preview = bool(device_id) and Device.objects.previews().filter(id=device_id, event_id=event_id).exists()
        if not is_preview:
            return JsonResponse({'content_version': version, 'changed': version > since, 'preview': False,
                                 'retry_after': 300})
        from attnd import longpoll_asgi
        if longpoll_asgi.waited(request):  # the ASGI layer already waited, without a thread
            version = Event.objects.filter(id=event_id).values_list('content_version', flat=True).first()
            changed, waited = version is not None and version > since, True
        else:
            version, changed, waited = preview.wait_for_change(event_id, since)
        if version is None:
            return JsonResponse({'error': 'Not found'}, status=404)
        return JsonResponse({'content_version': version, 'changed': changed, 'preview': True,
                             'retry_after': 0 if waited else preview.RETRY_SECONDS})


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
        # Showcase events (home page gallery) aren't counted either.
        accepted = usage.ingest(event, platform, body.get('events'),
                                save=not is_ci_request(request) and not event.showcase)
        return JsonResponse({'status': 'ok', 'accepted': accepted})
