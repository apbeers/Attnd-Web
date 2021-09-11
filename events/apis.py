import decimal
from datetime import date, timedelta
from difflib import get_close_matches
from math import sqrt

from django.core.serializers import serialize
from django.http import HttpResponse, HttpResponseServerError
from django.shortcuts import get_list_or_404
from django.views import View

from events.models import Event, Location, Notification


class SingleEventView(View):

    def get(self, request, event_code):
        event = get_list_or_404(klass=Event, event_code=event_code)
        serialized_event = serialize(format='json', queryset=event)
        return HttpResponse(content=serialized_event, content_type='application/json')


class SingleLocationView(View):

    def get(self, request, event_id, location_id):
        location = get_list_or_404(klass=Location, id=location_id)
        serialized_location = serialize(format='json', queryset=location)
        return HttpResponse(content=serialized_location, content_type='application/json')


class AllLocationsView(View):

    def get(self, request, event_id):
        locations = get_list_or_404(klass=Location, event__id=event_id)
        serialized_locations = serialize(format='json', queryset=locations)
        return HttpResponse(content=serialized_locations, content_type='application/json')


class AllNotificationsView(View):

    def get(self, request, event_id):
        notifications = get_list_or_404(klass=Notification, event__id=event_id)
        serialized_notifications = serialize(format='json', queryset=notifications)
        return HttpResponse(content=serialized_notifications, content_type='application/json')


class NearbyEventsView(View):

    def get(self, request, latitude, longitude):
        try:
            latitude = decimal.Decimal(latitude)
            longitude = decimal.Decimal(longitude)
        except Exception:
            return HttpResponseServerError()

        radius = decimal.Decimal(0.5)  # 35 miles
        events = Event.objects.filter(public=True,
                                      latitude__lte=latitude + radius,
                                      latitude__gte=latitude - radius,
                                      longitude__lte=longitude + radius,
                                      longitude__gte=longitude - radius,
                                      start_date__lte=date.today() + timedelta(days=7),
                                      start_date__gte=date.today() - timedelta(days=3))

        events_with_distance_from_user = []
        for event in events:
            latitude_distance_from_user = latitude - event.latitude
            longitude_distance_from_user = longitude - event.longitude
            distance = sqrt((latitude_distance_from_user * latitude_distance_from_user) + (longitude_distance_from_user * longitude_distance_from_user))
            distance_in_miles = round(distance * 69, 1)
            serialized_event = serialize('json', [event])
            events_with_distance_from_user.append({'event': serialized_event,
                                                   'distance': distance_in_miles})

        sorted_events_by_distance_to_user = sorted(events_with_distance_from_user, key=lambda i: i['distance'])
        return HttpResponse(content=sorted_events_by_distance_to_user, content_type='application/json')


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
