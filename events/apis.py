import decimal
from datetime import date, timedelta
from math import sqrt


from django.core.serializers import serialize
from django.forms import model_to_dict
from django.http import HttpResponse, HttpResponseServerError, JsonResponse
from django.shortcuts import get_list_or_404

from events.models import Event, Location, Notification


def get_single_event(request, event_code):
    event = get_list_or_404(klass=Event, event_code=event_code)
    serialized_event = serialize(format='json', queryset=event)
    return HttpResponse(content=serialized_event, content_type='application/json')


def get_single_location(request, event_id, location_id):
    location = get_list_or_404(klass=Location, id=location_id)
    serialized_location = serialize(format='json', queryset=location)
    return HttpResponse(content=serialized_location, content_type='application/json')


def get_all_locations(request, event_id):
    locations = get_list_or_404(klass=Location, event__id=event_id)
    serialized_locations = serialize(format='json', queryset=locations)
    return HttpResponse(content=serialized_locations, content_type='application/json')


def get_all_notifications(request, event_id):
    notifications = get_list_or_404(klass=Notification, event__id=event_id)
    serialized_notifications = serialize(format='json', queryset=notifications)
    return HttpResponse(content=serialized_notifications, content_type='application/json')


def get_nearby_events(request, latitude, longitude):
    # try:
    latitude = float(latitude)
    longitude = float(longitude)
    latitude = decimal.Decimal(latitude)
    longitude = decimal.Decimal(longitude)
    # except Exception:
    #     return HttpResponseServerError()

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


def get_event_by_code_or_name(request, event_code, name):
    pass
