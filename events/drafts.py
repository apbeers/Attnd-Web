"""Draft and published content (batch 6 §4, owner Nov 21, 2026).

The database rows are the **draft**: every console edit, Ask Attnd change and chat card saves into them at once, and
the organizer's preview phones (API-026) see them live. Attendees get the **published** copy (`PublishedBundle`):
the bundle as it was at the last **Publish changes**, built once per catalogue version. The apps' contract is
unchanged; attendees just see edits after Publish.

Always immediate (they bypass the draft): sent notifications (also urgent ones), the event's status, paid and listing,
the Studio website's address (`sync`: the published version moves on, the overlay in `attendee_bundle` takes them
from the live event).

- `publish(event, user)`: snapshot the draft, build the bundles, the attendees' `content_version` = the draft's;
  the normal `content_updated` push goes to attendees.
- `changes(event)`: field-level rows draft vs published ("5 unpublished changes").
- `discard(event, user)`: the draft back to the published copy (undoable like any save).
- An event becomes published (status) → its first snapshot. A migration snapshotted every published event.
"""
import copy
import json
from datetime import datetime

from django.conf import settings
from django.db import transaction
from django.utils import timezone

# The event's look in the apps: published with the content.
LOOK_FIELDS = ('display_config', 'layout_config', 'accent_color', 'logo_url', 'theme', 'theme_apps', 'live_activity')
LOOK_LABELS = {'display_config': 'App layout', 'layout_config': 'Screens', 'accent_color': 'Accent colour',
               'logo_url': 'Logo', 'theme': 'Colours', 'theme_apps': 'Colours in the apps',
               'live_activity': 'Live Activity'}
# Changes to these Event fields go straight to attendees (no Publish needed).
BYPASS_EVENT_FIELDS = {'status', 'paid', 'public'}
EVENT_LABELS = {'name': 'Name', 'description': 'Description', 'address': 'Address', 'latitude': 'Map pin',
                'longitude': 'Map pin', 'start_time': 'Starts', 'end_time': 'Ends', 'timezone': 'Time zone',
                'context': 'Details for the assistant', 'flyer': 'Flyer'}


def _json(value):
    return json.loads(json.dumps(value, default=str))


def source(event):
    """The draft as a comparable, restorable snapshot: content.snapshot + the look."""
    from events.content import snapshot
    data = snapshot(event)
    data['look'] = _json({f: getattr(event, f, None) for f in LOOK_FIELDS})
    data['event']['flyer'] = event.flyer.name if event.flyer else ''
    return _json(data)


def _request():
    """A request on the site's own origin, so the stored bundles carry absolute URLs like a live one."""
    from urllib.parse import urlsplit

    from django.test import RequestFactory
    site = urlsplit(getattr(settings, 'SITE_URL', '') or 'https://attnd.app')
    host = site.netloc or 'attnd.app'
    return RequestFactory(SERVER_NAME=host.split(':')[0]).get('/', HTTP_HOST=host, secure=site.scheme == 'https')


def _bundles(event):
    from events import content
    from events.layout import CATALOGUE_VERSION
    request = _request()
    return _json({str(v): content.bundle(event, request, v) for v in range(0, CATALOGUE_VERSION + 1)})


def published(event):
    from events.models import PublishedBundle
    return PublishedBundle.objects.filter(event=event).first()


def enabled(event):
    """Draft mode is on for events attendees can reach (published / archived) once they have a published copy."""
    return event.status != event.STATUS_DRAFT


def publish(event, user=None, push=True):
    """Snapshot the draft for attendees and tell their phones."""
    from events.models import Event, PublishedBundle
    with transaction.atomic():
        event = Event.objects.select_for_update().get(pk=event.pk)
        pb, _ = PublishedBundle.objects.update_or_create(event=event, defaults={
            'version': event.content_version, 'source': source(event), 'bundles': _bundles(event),
            'published_at': timezone.now(), 'published_by': user if user and user.is_authenticated else None})
    if push:
        from events import push as push_module
        push_module.content_changed(event.pk)  # version == published: the attendees' push
    return pb


def ensure(event):
    """An event attendees can reach always has a published copy (made from the rows the first time: before §4
    every edit was live, so that is what attendees saw)."""
    pb = published(event)
    if pb is None and enabled(event):
        pb = publish(event, push=False)
    return pb


def sync(event_id):
    """A change that bypasses the draft (a notification sent, the status): attendees get it at once."""
    from events.models import Event, PublishedBundle
    version = Event.objects.filter(pk=event_id).values_list('content_version', flat=True).first()
    if version is not None:
        PublishedBundle.objects.filter(event_id=event_id, version__lt=version).update(version=version)


def attendee_version(event_id):
    """The content_version attendees see, or None when the event has no published copy (serve the draft)."""
    from events.models import Event, PublishedBundle
    return (PublishedBundle.objects.filter(event_id=event_id).exclude(event__status=Event.STATUS_DRAFT)
            .values_list('version', flat=True).first())


def attendee_bundle(event, pb, sections, request=None):
    """The published bundle for an app on catalogue `sections`, with what bypasses the draft taken live."""
    from events import content
    bundles = pb.bundles or {}
    key = str(sections) if str(sections) in bundles else str(max((int(k) for k in bundles if int(k) <= sections),
                                                                default=0))
    data = copy.deepcopy(bundles.get(key) or {})
    data['content_version'] = pb.version
    live = content.event_v2(event, request)
    for k in ('status', 'paid', 'is_demo', 'public', 'website_url'):
        if k in live and 'event' in data:
            data['event'][k] = live[k]
    data['notifications'] = [content.notification_v2(n) for n in
                             event.notification_set.filter(sent=True).order_by('-send_time')]
    return data


# ---------------------------------------------------------------------------
# What changed since the last Publish
# ---------------------------------------------------------------------------

def _short(value):
    if value in (None, '', [], {}):
        return '—'
    if isinstance(value, (dict, list)):
        return 'changed'
    text = str(value)
    if len(text) >= 19 and text[4] == '-' and 'T' in text:
        try:
            return timezone.localtime(datetime.fromisoformat(text)).strftime('%a %b %-d, %-I:%M %p')
        except ValueError:
            pass
    return text if len(text) <= 80 else text[:79] + '…'


LOCATION_LABELS = {'name': 'Name', 'snippet': 'Summary', 'description': 'Description', 'category': 'Category',
                   'tags': 'Tags', 'address': 'Address', 'latitude': 'Map pin', 'longitude': 'Map pin',
                   'image_url': 'Photo', 'image': 'Photo', 'website': 'Website', 'context': 'Details',
                   'hidden': 'Hidden', 'sort_order': 'Order'}
ITEM_LABELS = {'name': 'Name', 'description': 'Description', 'start_time': 'Starts', 'end_time': 'Ends',
               'location_id': 'Place', 'tags': 'Tags', 'context': 'Details', 'hidden': 'Hidden'}


def _diff_rows(kind, name, before, after, labels, skip=('locked_fields', 'id')):
    rows, seen = [], set()
    for field in after:
        if field in skip or before.get(field) == after.get(field):
            continue
        label = labels.get(field, field.replace('_', ' ').capitalize())
        if label in seen:
            continue
        seen.add(label)
        rows.append({'kind': kind, 'name': name, 'field': label, 'old': _short(before.get(field)),
                     'new': _short(after.get(field)), 'change': 'changed'})
    return rows


def changes(event, pb=None):
    """Field-level rows draft vs published: [{kind, name, field, old, new, change}] (empty when nothing changed)."""
    pb = pb or published(event)
    if pb is None or not enabled(event):
        return []
    old, new = pb.source, source(event)
    rows = _diff_rows('Event', event.name, old.get('event', {}), new['event'], EVENT_LABELS)
    for field in LOOK_FIELDS:
        if (old.get('look') or {}).get(field) != new['look'].get(field):
            rows.append({'kind': 'App', 'name': event.name, 'field': LOOK_LABELS[field], 'old': '', 'new': '',
                         'change': 'changed'})
    for key, kind, labels in (('locations', 'Place', LOCATION_LABELS), ('schedule', 'Schedule', ITEM_LABELS)):
        before = {x['id']: x for x in old.get(key, [])}
        after = {x['id']: x for x in new[key]}
        for oid, row in after.items():
            if oid not in before:
                rows.append({'kind': kind, 'name': row.get('name', ''), 'field': '', 'old': '', 'new': '',
                             'change': 'added'})
            else:
                rows += _diff_rows(kind, row.get('name', ''), before[oid], row, labels)
        for oid, row in before.items():
            if oid not in after:
                rows.append({'kind': kind, 'name': row.get('name', ''), 'field': '', 'old': '', 'new': '',
                             'change': 'removed'})
    return rows


def has_changes(event):
    """Cheap hint (no diff): the draft moved past the attendees' version."""
    pb = published(event)
    return bool(pb and enabled(event) and changes(event, pb))


def phones(event):
    from events.models import Device
    return Device.objects.attendees().filter(event=event).count()


def discard(event, user=None):
    """The draft back to the published copy (one transaction; the Undo recorder sees ordinary saves)."""
    from events.content import restore
    pb = published(event)
    if pb is None:
        return event

    class _Revision:  # restore() reads .event, .snapshot, .content_version
        pass
    rev = _Revision()
    rev.event, rev.snapshot, rev.content_version = event, pb.source, pb.version
    with transaction.atomic():
        restore(rev, user)
        event.refresh_from_db()
        for field, value in (pb.source.get('look') or {}).items():
            if hasattr(event, field):
                setattr(event, field, value)
        event.save()
    return event
