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


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------

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
        'latitude': float(event.latitude),
        'longitude': float(event.longitude),
        'start_time': event.start_time.isoformat(),
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
        'location_name': item.location.name,
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
        for item in location.scheduleditem_set.order_by('start_time')
    ]
    return {
        'id': location.id,
        'name': location.name,
        'snippet': location.snippet,
        'description': location.description,
        'website': location.website,
        'image_url': image_url,
        'latitude': float(location.latitude),
        'longitude': float(location.longitude),
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


def _relevance_score(distance_miles, start_time, now):
    hours_until = (start_time - now).total_seconds() / 3600
    time_score = abs(hours_until) / 24
    distance_score = distance_miles / 10
    return time_score * 0.6 + distance_score * 0.4


# ---------------------------------------------------------------------------
# Auth mixin
# ---------------------------------------------------------------------------

class ApiKeyRequired:
    def dispatch(self, request, *args, **kwargs):
        key = request.headers.get('Authorization', '').removeprefix('Bearer ')
        if not key or key != settings.API_KEY:
            return JsonResponse({'error': 'Unauthorized'}, status=401)
        return super().dispatch(request, *args, **kwargs)


# ---------------------------------------------------------------------------
# Event endpoints
# ---------------------------------------------------------------------------

@method_decorator(csrf_exempt, name='dispatch')
class SingleEventView(ApiKeyRequired, View):

    def get(self, request, event_id):
        event = Event.objects.filter(id=event_id).first()
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

        radius = decimal.Decimal(0.5)
        now = timezone.now()
        events = Event.objects.filter(
            public=True,
            latitude__lte=latitude + radius,
            latitude__gte=latitude - radius,
            longitude__lte=longitude + radius,
            longitude__gte=longitude - radius,
            start_time__lte=now + timedelta(days=7),
            start_time__gte=now - timedelta(days=3),
        )

        items = []
        for event in events:
            lat_diff = float(latitude - event.latitude)
            lon_diff = float(longitude - event.longitude)
            distance_miles = round(sqrt(lat_diff ** 2 + lon_diff ** 2) * 69, 1)
            starts_in_hours = round((event.start_time - now).total_seconds() / 3600, 1)
            score = _relevance_score(distance_miles, event.start_time, now)
            items.append((score, {
                'event': _event_dict(event, request),
                'distance_miles': distance_miles,
                'starts_in_hours': starts_in_hours,
            }))

        items.sort(key=lambda x: x[0])
        results = [item for _, item in items]
        return JsonResponse({'results': results, 'count': len(results)})


@method_decorator(csrf_exempt, name='dispatch')
class EventByCodeOrNameView(ApiKeyRequired, View):

    def get(self, request):
        name_or_code = request.GET.get('data', '')
        candidates = []
        for name, code in Event.objects.filter(public=True).values_list('name', 'event_code'):
            candidates.append(name)
            candidates.append(code)

        matches = get_close_matches(word=name_or_code, possibilities=candidates, n=1)
        if not matches:
            return JsonResponse({'error': 'Not found'}, status=404)

        match = matches[0]
        event = (
            Event.objects.filter(public=True, name=match).first()
            or Event.objects.filter(public=True, event_code=match).first()
        )
        if event is None:
            return JsonResponse({'error': 'Not found'}, status=404)
        return JsonResponse(_event_dict(event, request))


@method_decorator(csrf_exempt, name='dispatch')
class EventLookupView(ApiKeyRequired, View):
    """Exact match by name or event_code. Does not filter by public — for organizer use."""

    def get(self, request):
        q = request.GET.get('q', '').strip()
        if not q:
            return JsonResponse({'error': 'Missing q parameter'}, status=400)
        event = (
            Event.objects.filter(name__iexact=q).first()
            or Event.objects.filter(event_code__iexact=q).first()
        )
        if event is None:
            return JsonResponse({'error': 'Not found'}, status=404)
        return JsonResponse(_event_dict(event, request))


# ---------------------------------------------------------------------------
# Location endpoints
# ---------------------------------------------------------------------------

@method_decorator(csrf_exempt, name='dispatch')
class SingleLocationView(ApiKeyRequired, View):

    def get(self, request, event_id, location_id):
        location = Location.objects.filter(event__id=event_id, id=location_id).first()
        if location is None:
            return JsonResponse({'error': 'Not found'}, status=404)
        return JsonResponse(_location_dict(location, request))


@method_decorator(csrf_exempt, name='dispatch')
class AllLocationsView(ApiKeyRequired, View):

    def get(self, request, event_id):
        locations = Location.objects.filter(event__id=event_id).order_by('name')
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
            .filter(event_id=event_id)
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

@method_decorator(csrf_exempt, name='dispatch')
class JoinEventView(ApiKeyRequired, View):

    def post(self, request, event_id, device_id):
        event = Event.objects.filter(id=event_id).first()
        if event is None:
            return JsonResponse({'error': 'Event not found'}, status=404)

        try:
            body = json.loads(request.body)
            platform = body.get('platform', '')
        except (json.JSONDecodeError, AttributeError):
            platform = ''

        device, created = Device.objects.get_or_create(
            id=device_id,
            defaults={'event': event, 'platform': platform},
        )
        if not created:
            device.event = event
            if platform:
                device.platform = platform
            device.save()
        return JsonResponse({'status': 'ok'})


@method_decorator(csrf_exempt, name='dispatch')
class LeaveEventView(ApiKeyRequired, View):

    def post(self, request, device_id, event_id):
        Device.objects.filter(id=device_id).update(event=None)
        return JsonResponse({'status': 'ok'})


@method_decorator(csrf_exempt, name='dispatch')
class PushNotificationsSubscribe(ApiKeyRequired, View):

    def post(self, request, device_id):
        Device.objects.filter(id=device_id).update(subscribed=True)
        return JsonResponse({'status': 'ok'})


@method_decorator(csrf_exempt, name='dispatch')
class PushNotificationsUnSubscribe(ApiKeyRequired, View):

    def post(self, request, device_id):
        Device.objects.filter(id=device_id).update(subscribed=False)
        return JsonResponse({'status': 'ok'})


# ---------------------------------------------------------------------------
# Internal: send queued notifications (no auth — called by scheduler)
# ---------------------------------------------------------------------------

@method_decorator(csrf_exempt, name='dispatch')
class SendNotifications(View):

    def post(self, request):
        now = timezone.now()
        events = Event.objects.filter(
            start_time__lte=now + timedelta(days=7),
            start_time__gte=now - timedelta(days=3),
        )
        for event in events:
            notifications = Notification.objects.filter(event=event, sent=False, send_time__lte=now)
            devices = Device.objects.filter(event=event, subscribed=True)
            for notification in notifications:
                for device in devices:
                    if device.platform == 'ios':
                        self.send_ios_notification(notification)
                    elif device.platform == 'android':
                        self.send_android_notification(notification)
                notification.sent = True
                notification.save()
        return JsonResponse({'status': 'ok'})

    def send_ios_notification(self, notification):
        pass

    def send_android_notification(self, notification):
        pass
