"""Duplicate or roll over an event (stage I12): a new draft with every location, schedule item and the details for
the assistant copied, times shifted to the new start (same weekday by default: "next year's festival"), the team
kept. Never copied: attendees' devices, notifications, usage counts, the website watch state, History."""

from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from events.models import Event, EventMember, Location, ScheduledItem, generate_event_code


def default_shift(event):
    """The same weekday about a year later (52 weeks; 53 when that lands more than 3 days early)."""
    weeks = 52
    if event.start_time and (event.start_time + timedelta(weeks=52)).date() < (
            event.start_time.replace(year=event.start_time.year + 1) - timedelta(days=3)).date():
        weeks = 53
    return timedelta(weeks=weeks)


def shift_for(event, new_start_date):
    """The shift that moves the event's first day to `new_start_date` (a date), keeping the clock times in its zone."""
    from scrape.diff import tz_or_utc
    tz = tz_or_utc(event.timezone)
    local = timezone.localtime(event.start_time, tz)
    return timedelta(days=(new_start_date - local.date()).days)


@transaction.atomic
def duplicate(event, user, shift, name=None, keep_team=True):
    """The new draft event."""
    old_pk = event.pk
    copy = Event.objects.get(pk=old_pk)
    copy.pk = None
    copy.id = None
    copy.name = (name or event.name)[:256]
    copy.event_code = generate_event_code()
    copy.status = Event.STATUS_DRAFT
    copy.start_time = event.start_time + shift
    copy.end_time = event.end_time + shift if event.end_time else None
    copy.user = user if not event.user_id else event.user
    copy.content_version = 1
    copy.content_updated_at = timezone.now()
    copy.created_at = timezone.now()
    copy.last_refreshed_at = copy.sources_checked_at = None
    copy.refresh_schedule = Event.REFRESH_OFF  # the old website is last year's: watch again only when asked
    copy.plan_suggestions = {}
    copy.public = False
    copy.paid = False
    copy.save()
    places = {}
    for loc in Location.objects.filter(event_id=old_pk).order_by('sort_order', 'pk'):
        old = loc.pk
        loc.pk = None
        loc.id = None
        loc.event = copy
        loc.save()
        places[old] = loc
    ScheduledItem.objects.bulk_create([
        ScheduledItem(event=copy, name=it.name, description=it.description, start_time=it.start_time + shift,
                      end_time=it.end_time + shift if it.end_time else None,
                      location=places.get(it.location_id), tags=it.tags, context=it.context, hidden=it.hidden,
                      locked_fields=it.locked_fields)
        for it in ScheduledItem.objects.filter(event_id=old_pk)])
    if keep_team:
        EventMember.objects.bulk_create([EventMember(event=copy, user_id=m.user_id, role=m.role)
                                         for m in EventMember.objects.filter(event_id=old_pk)
                                         if m.user_id != copy.user_id])
    return copy
