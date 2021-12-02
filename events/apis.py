import decimal
from datetime import date, timedelta, datetime
from difflib import get_close_matches

from django.core.serializers import serialize
from django.http import HttpResponse, HttpResponseServerError
from django.views import View
from rest_framework.response import Response
from rest_framework.views import APIView

from events.models import Event, Location, Notification, Device
from events.serializers import LocationSerializer, NotificationSerializer, EventSerializer, NearbyEventSerializer


class JoinEventView(View):

    def post(self, request, event_id, device_id):
        event = Event.objects.filter(id=event_id).get()
        device, created = Device.objects.get_or_create(id=device_id, defaults={'event': event})
        if not created:
            device.event = event
            device.save()
        return HttpResponse()


class LeaveEventView(View):

    def post(self, request, device_id, event_id):
        Device.objects.filter(id=device_id).update(event=None)
        return HttpResponse


class SingleEventView(APIView):

    def get(self, request, event_id):
        event = Event.objects.filter(id=event_id).first()
        serializer = EventSerializer([event], many=True, context={'request': request})
        return Response(serializer.data)


class AllLocationsView(APIView):

    def get(self, request, event_id):
        locations = Location.objects.filter(event__id=event_id).order_by('name')
        serializer = LocationSerializer(locations, many=True, context={"request": request})
        return Response(serializer.data)


class AllNotificationsView(APIView):

    def get(self, request, event_id):
        notifications = Notification.objects.filter(sent=True, event__id=event_id).order_by('-send_time')
        serializer = NotificationSerializer(notifications, many=True)
        return Response(serializer.data)


class NearbyEventsView(APIView):

    def get(self, request, latitude, longitude):
        try:
            latitude = decimal.Decimal(latitude)
            longitude = decimal.Decimal(longitude)
        except Exception:
            return HttpResponseServerError()

        radius = decimal.Decimal(0.5)  # ~35 miles
        events = Event.objects.filter(latitude__lte=latitude + radius,
                                      latitude__gte=latitude - radius,
                                      longitude__lte=longitude + radius,
                                      longitude__gte=longitude - radius,
                                      start_time__lte=date.today() + timedelta(days=7),
                                      start_time__gte=date.today() - timedelta(days=3))

        serializer = NearbyEventSerializer(events, many=True, context={'latitude': latitude, 'longitude': longitude})
        sorted_events_by_distance_to_user = sorted(serializer.data, key=lambda i: i['distance'])
        return Response(sorted_events_by_distance_to_user)


class EventByCodeOrNameView(View):

    def get(self, request):
        name_or_code = request.GET.get('data')
        events = Event.objects.values_list('name', 'event_code')
        event_names_and_codes = []
        for event in events:
            event_names_and_codes.append(event[0])
            event_names_and_codes.append(event[1])

        matches = get_close_matches(word=name_or_code, possibilities=event_names_and_codes, n=1)

        if matches:
            event_by_name = Event.objects.filter(name=matches[0]).first()
            event_by_code = Event.objects.filter(event_code=matches[0]).first()

            if event_by_name:
                serialized_event = serialize('json', [event_by_name])
                return HttpResponse(content=serialized_event, content_type='application/json')
            elif event_by_code:
                serialized_event = serialize('json', [event_by_code])
                return HttpResponse(content=serialized_event, content_type='application/json')
        else:
            return HttpResponseServerError()


class PushNotificationsSubscribe(View):

    def post(self, request, device_id):
        Device.objects.filter(id=device_id).update(subscribed=True)
        return HttpResponse()


class PushNotificationsUnSubscribe(View):

    def post(self, request, device_id):
        Device.objects.filter(id=device_id).update(subscribed=False)
        return HttpResponse()


class SendNotifications(View):

    def post(self):
        events = Event.objects.filter(start_time__lte=date.today() + timedelta(days=7),
                                      start_time__gte=date.today() - timedelta(days=3))

        for event in events:
            notifications = Notification.objects.filter(event=event, sent=False, send_time__lte=datetime.now())
            devices = Device.objects.filter(event=event, subscribed=True)
            for notification in notifications:
                for device in devices:
                    if device.platform == 'android':
                        self.send_android_notification(notification)
                    elif device.platform == 'ios':
                        self.send_ios_notification(notification)

                notification.sent = True
                notification.save()

    def send_ios_notification(self, notification):
        pass

    def send_android_notification(self, notification):
        pass
