"""Background tasks of the events app (django.tasks, stage F5); see background/__init__.py."""

from django.tasks import task


@task
def find_place_image(location_id):
    from events import place_images
    from events.models import Location
    location = Location.objects.select_related('event').filter(pk=location_id).first()
    return place_images.find_for(location) if location is not None else 'gone'


@task
def refresh_plan_suggestions(event_id):
    from events import plan_suggestions
    from events.models import Event
    event = Event.objects.filter(pk=event_id).exclude(status=Event.STATUS_DRAFT).first()
    if event is not None and plan_suggestions.due(event):
        plan_suggestions.generate(event)
        return 'generated'
    return 'not due'
