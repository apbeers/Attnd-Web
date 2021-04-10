from django.http import JsonResponse, HttpResponse
from django.shortcuts import get_list_or_404
from django.core.serializers import serialize
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
