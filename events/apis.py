import decimal
from datetime import timedelta
from difflib import get_close_matches
from math import sqrt

from django.conf import settings
from django.http import JsonResponse
from django.utils import timezone
from django.views import View

from events.models import Event, Location, Notification, Device


# ---------------------------------------------------------------------------
# Serialization helpers — return clean flat dicts, no Django wrapper
# ---------------------------------------------------------------------------

def _event_dict(event):
    return {
        'id': event.id,
        'name': event.name,
        'event_code': event.event_code,
        'description': event.description,
        'latitude': float(event.latitude),
        'longitude': float(event.longitude),
        'start_time': event.start_time.isoformat(),
        'paid': event.paid,
        'flyer': str(event.flyer) if event.flyer else None,
        'address': event.address,
    }


def _location_dict(location):
    return {
        'id': location.id,
        'name': location.name,
        'snippet': location.snippet,
        'description': location.description,
        'website': location.website,
        'image': str(location.image) if location.image else None,
        'latitude': float(location.latitude),
        'longitude': float(location.longitude),
        'address': location.address,
    }


def _notification_dict(notification):
    return {
        'id': notification.id,
        'title': notification.title,
        'body': notification.body,
        'send_time': notification.send_time.isoformat(),
        'sent': notification.sent,
    }


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

class SingleEventView(ApiKeyRequired, View):

    def get(self, request, event_id):
        event = Event.objects.filter(id=event_id).first()
        if event is None:
            return JsonResponse({'error': 'Not found'}, status=404)
        return JsonResponse(_event_dict(event))


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
            latitude__lte=latitude + radius,
            latitude__gte=latitude - radius,
            longitude__lte=longitude + radius,
            longitude__gte=longitude - radius,
            start_time__lte=now + timedelta(days=7),
            start_time__gte=now - timedelta(days=3),
        )

        results = []
        for event in events:
            lat_diff = float(latitude - event.latitude)
            lon_diff = float(longitude - event.longitude)
            distance_miles = round(sqrt(lat_diff ** 2 + lon_diff ** 2) * 69, 1)
            results.append({'event': _event_dict(event), 'distance': distance_miles})

        results.sort(key=lambda r: r['distance'])
        return JsonResponse(results, safe=False)


class EventByCodeOrNameView(ApiKeyRequired, View):

    def get(self, request):
        name_or_code = request.GET.get('data', '')
        candidates = []
        for name, code in Event.objects.values_list('name', 'event_code'):
            candidates.append(name)
            candidates.append(code)

        matches = get_close_matches(word=name_or_code, possibilities=candidates, n=1)
        if not matches:
            return JsonResponse({'error': 'Not found'}, status=404)

        match = matches[0]
        event = Event.objects.filter(name=match).first() or Event.objects.filter(event_code=match).first()
        if event is None:
            return JsonResponse({'error': 'Not found'}, status=404)
        return JsonResponse(_event_dict(event))


# ---------------------------------------------------------------------------
# Location endpoints
# ---------------------------------------------------------------------------

class SingleLocationView(ApiKeyRequired, View):

    def get(self, request, event_id, location_id):
        location = Location.objects.filter(event__id=event_id, id=location_id).first()
        if location is None:
            return JsonResponse({'error': 'Not found'}, status=404)
        return JsonResponse(_location_dict(location))


class AllLocationsView(ApiKeyRequired, View):

    def get(self, request, event_id):
        locations = Location.objects.filter(event__id=event_id).order_by('name')
        return JsonResponse([_location_dict(loc) for loc in locations], safe=False)


# ---------------------------------------------------------------------------
# Notification endpoints
# ---------------------------------------------------------------------------

class SingleNotificationView(ApiKeyRequired, View):

    def get(self, request, event_id, notification_id):
        notification = Notification.objects.filter(event__id=event_id, id=notification_id).first()
        if notification is None:
            return JsonResponse({'error': 'Not found'}, status=404)
        return JsonResponse(_notification_dict(notification))


class AllNotificationsView(ApiKeyRequired, View):

    def get(self, request, event_id):
        notifications = Notification.objects.filter(sent=True, event__id=event_id).order_by('-send_time')
        return JsonResponse([_notification_dict(n) for n in notifications], safe=False)


# ---------------------------------------------------------------------------
# Device / membership endpoints
# ---------------------------------------------------------------------------

class JoinEventView(ApiKeyRequired, View):

    def post(self, request, event_id, device_id):
        event = Event.objects.filter(id=event_id).first()
        if event is None:
            return JsonResponse({'error': 'Event not found'}, status=404)
        device, created = Device.objects.get_or_create(id=device_id, defaults={'event': event})
        if not created:
            device.event = event
            device.save()
        return JsonResponse({'status': 'ok'})


class LeaveEventView(ApiKeyRequired, View):

    def post(self, request, device_id, event_id):
        Device.objects.filter(id=device_id).update(event=None)
        return JsonResponse({'status': 'ok'})


class PushNotificationsSubscribe(ApiKeyRequired, View):

    def post(self, request, device_id):
        Device.objects.filter(id=device_id).update(subscribed=True)
        return JsonResponse({'status': 'ok'})


class PushNotificationsUnSubscribe(ApiKeyRequired, View):

    def post(self, request, device_id):
        Device.objects.filter(id=device_id).update(subscribed=False)
        return JsonResponse({'status': 'ok'})


# ---------------------------------------------------------------------------
# Internal: send queued notifications (no auth — called by scheduler)
# ---------------------------------------------------------------------------

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
