"""API-010: schedule item tags become lower-case, like the new location tags.

Existing items are normalised once here (later saves normalise in ScheduledItem.save), and each event with a
changed item gets a new content_version, so apps refetch the bundle instead of keeping a cached copy.
"""
from django.db import migrations
from django.db.models import F
from django.utils import timezone


def normalize(apps, schema_editor):
    from events.models import normalize_tags  # pure function, no model state involved
    ScheduledItem = apps.get_model('events', 'ScheduledItem')
    Event = apps.get_model('events', 'Event')
    changed_events = set()
    for item in ScheduledItem.objects.exclude(tags=[]).only('id', 'tags', 'event_id').iterator():
        tags = normalize_tags(item.tags)
        if tags != item.tags:
            ScheduledItem.objects.filter(pk=item.pk).update(tags=tags)
            changed_events.add(item.event_id)
    if changed_events:
        Event.objects.filter(pk__in=changed_events).update(content_version=F('content_version') + 1,
                                                          content_updated_at=timezone.now())


class Migration(migrations.Migration):
    dependencies = [('events', '0033_tags_priority_live_activity')]
    operations = [migrations.RunPython(normalize, migrations.RunPython.noop)]
