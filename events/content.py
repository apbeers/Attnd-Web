"""
Serialization, snapshots and version bumping for event content.

Shared by the v1/v2 API, the organizer console, and the AI change engine.
"""

import contextlib
import re
import threading

from django.conf import settings
from django.db import transaction
from django.utils.dateparse import parse_datetime

from events import context as ctx
from events.models import Event, EventRevision, Location, Notification, ScheduledItem, normalize_tags

_state = threading.local()


# ---------------------------------------------------------------------------
# Version bumping
# ---------------------------------------------------------------------------

@contextlib.contextmanager
def batch_changes(event):
    """Suppress per-row version bumps inside the block; bump once at the end."""
    depth = getattr(_state, 'depth', 0)
    _state.depth = depth + 1
    try:
        yield
    finally:
        _state.depth = depth
    if depth == 0 and event.pk:
        event.bump_version()


def maybe_bump(event_id):
    if getattr(_state, 'depth', 0):
        return
    event = Event.objects.filter(pk=event_id).first()
    if event:
        event.bump_version()


# ---------------------------------------------------------------------------
# Serialization (v2)
# ---------------------------------------------------------------------------

def _dt(value):
    return value.isoformat() if value else None


def _coord(value):
    return float(value) if value is not None else None


def _abs(request, url):
    if not url:
        return None
    if url.startswith(('http://', 'https://')) or request is None:
        return url
    return request.build_absolute_uri(url)


# Phone-sized images (v2 only): Cloudinary delivers a resized, recompressed copy in the best format the app
# accepts (f_auto: WebP/AVIF/JPEG), so the apps download a fraction of an organizer's original photo.
# Deterministic, so the URL stays the same for the same image (the apps cache images by URL).
APP_IMAGE_WIDTH = {'location': 1200, 'logo': 512, 'flyer': 2000}
_CLOUDINARY_UPLOAD = re.compile(r'^(https?://res\.cloudinary\.com/[^/]+/image/upload/)(.+)$')
_RASTER = ('.jpg', '.jpeg', '.png', '.webp', '.heic', '.heif', '.avif', '.bmp', '.tif', '.tiff')
_TRANSFORM_SEGMENT = re.compile(r'^[a-z]{1,3}_[^/]*$')  # e.g. w_800,c_fill (already transformed: leave it)


def app_image_url(url, kind):
    """`url` with Cloudinary delivery transformations for phones, or unchanged (other hosts, PDFs, GIFs,
    URLs that already carry a transformation)."""
    if not url:
        return url
    match = _CLOUDINARY_UPLOAD.match(url)
    if not match:
        return url
    rest = match.group(2)
    if not rest.split('?', 1)[0].lower().endswith(_RASTER) or _TRANSFORM_SEGMENT.match(rest.split('/', 1)[0]):
        return url
    return f'{match.group(1)}f_auto,q_auto,c_limit,w_{APP_IMAGE_WIDTH[kind]}/{rest}'


def event_v2(event, request=None):
    return {
        'id': event.id,
        'name': event.name,
        'event_code': event.event_code,
        'description': event.description,
        'address': event.address,
        'latitude': _coord(event.latitude),
        'longitude': _coord(event.longitude),
        'start_time': _dt(event.start_time),
        'end_time': _dt(event.end_time),
        'timezone': event.timezone,
        'status': event.status,
        'paid': event.paid,
        'is_demo': not event.paid,
        'flyer_url': app_image_url(_abs(request, event.flyer.url), 'flyer') if event.flyer else None,
        'accent_color': event.accent_color or None,
        'logo_url': app_image_url(event.logo_url, 'logo') or None,
        'content_version': event.content_version,
        'join_url': f'https://attnd.app/join/{event.event_code}/',
        # API-016: the event's Attnd Studio website while it is published, else null.
        'website_url': _website_url(event),
        # API-017: the event's ticket page on attnd.app while it sells tickets through Attnd, else null.
        'tickets_url': _tickets_url(event),
    }


def _tickets_url(event):
    if not settings.TICKETS_ENABLED:  # ticketing parked: always null (the key stays for installed apps)
        return None
    from tickets.content import tickets_url
    return tickets_url(event)


def _website_url(event):
    from studio.publishing import website_url
    return website_url(event)


def location_v2(location, request=None):
    return {
        'id': location.id,
        'name': location.name,
        'snippet': location.snippet,
        'description': location.description,
        'category': location.category or None,
        'website': location.website or None,
        'image_url': app_image_url(_abs(request, location.resolved_image_url), 'location'),
        'address': location.address,
        'latitude': _coord(location.latitude),
        'longitude': _coord(location.longitude),
        'sort_order': location.sort_order,
        'tags': normalize_tags(location.tags),
        'context': ctx.for_bundle(ctx.LOCATION, location.context),  # API-018
    }


def item_v2(item):
    return {
        'id': item.id,
        'name': item.name,
        'description': item.description,
        'start_time': _dt(item.start_time),
        'end_time': _dt(item.end_time),
        'location_id': item.location_id,
        'location_name': item.location.name if item.location_id else None,
        'tags': normalize_tags(item.tags),
        'context': ctx.for_bundle(ctx.ITEM, item.context),  # API-018
    }


def notification_v2(n):
    return {
        'id': n.id,
        'title': n.title,
        'body': n.body,
        'send_time': _dt(n.sent_at or n.send_time),
        'priority': n.priority or Notification.PRIORITY_NORMAL,
    }


def bundle(event, request=None):
    from events.display import event_stats, resolve_display

    locations = list(event.location_set.filter(hidden=False).order_by('sort_order', 'name'))
    items = list(
        event.scheduleditem_set.filter(hidden=False)
        .filter(models_q_visible_location())
        .select_related('location').order_by('start_time', 'name')
    )
    notifications = list(event.notification_set.filter(sent=True).order_by('-send_time'))
    stats = event_stats(event, locations)
    categories = sorted({loc.category for loc in locations if loc.category})
    location_rows = [location_v2(loc, request) for loc in locations]
    item_rows = [item_v2(i) for i in items]
    # API-018: the event's assistant context only in the bundle (not in lookup / nearby), within one budget.
    event_row = event_v2(event, request)
    event_row['context'] = ctx.for_bundle(ctx.EVENT, event.context, location_ids={loc.id for loc in locations})
    ctx.apply_budget(event_row['context'], location_rows, item_rows, label=f'event {event.pk}')
    return {
        'schema_version': 2,
        'content_version': event.content_version,
        'updated_at': _dt(event.content_updated_at),
        'event': event_row,
        'display': resolve_display(event, stats),
        'categories': categories,
        'locations': location_rows,
        'schedule': item_rows,
        'notifications': [notification_v2(n) for n in notifications],
        'tickets': _tickets_summary(event),  # API-017, null without Attnd ticketing
    }


def _tickets_summary(event):
    if not settings.TICKETS_ENABLED:  # ticketing parked: always null
        return None
    from tickets.content import summary
    return summary(event)


def models_q_visible_location():
    from django.db.models import Q
    return Q(location__isnull=True) | Q(location__hidden=False)


# ---------------------------------------------------------------------------
# Snapshots & rollback
# ---------------------------------------------------------------------------

def snapshot(event):
    """Full, restorable copy of an event's editable content."""
    locations = []
    for loc in event.location_set.all().order_by('id'):
        locations.append({
            'id': loc.id,
            **{f: (str(getattr(loc, f)) if f in ('latitude', 'longitude') and getattr(loc, f) is not None
                   else getattr(loc, f)) for f in Location.CONTENT_FIELDS},
            'sort_order': loc.sort_order,
            'hidden': loc.hidden,
            'locked_fields': loc.locked_fields,
        })
    items = []
    for item in event.scheduleditem_set.all().order_by('id'):
        items.append({
            'id': item.id,
            'name': item.name,
            'description': item.description,
            'start_time': _dt(item.start_time),
            'end_time': _dt(item.end_time),
            'location_id': item.location_id,
            'tags': item.tags,
            'context': item.context,
            'hidden': item.hidden,
            'locked_fields': item.locked_fields,
        })
    return {
        'event': {
            'name': event.name,
            'description': event.description,
            'address': event.address,
            'latitude': str(event.latitude) if event.latitude is not None else None,
            'longitude': str(event.longitude) if event.longitude is not None else None,
            'start_time': _dt(event.start_time),
            'end_time': _dt(event.end_time),
            'timezone': event.timezone,
            'context': event.context,
            'locked_fields': event.locked_fields,
        },
        'locations': locations,
        'schedule': items,
        'tickets': _tickets_snapshot(event),
    }


def _tickets_snapshot(event):
    if not settings.TICKETS_ENABLED:  # ticketing parked: revisions carry no ticket types
        return None
    from tickets.content import snapshot as tickets_snapshot
    return tickets_snapshot(event)


def save_revision(event, summary, user=None):
    return EventRevision.objects.create(
        event=event,
        content_version=event.content_version,
        snapshot=snapshot(event),
        summary=summary[:256],
        created_by=user if (user and user.is_authenticated) else None,
    )


def restore(revision, user=None):
    """Replace the event's content with a revision's snapshot (itself saving a revision first)."""
    event = revision.event
    data = revision.snapshot
    with transaction.atomic():
        save_revision(event, f'Before restoring version {revision.content_version}', user)
        with batch_changes(event):
            ev = data['event']
            for f in ('name', 'description', 'address', 'timezone', 'locked_fields'):
                setattr(event, f, ev[f])
            event.latitude = ev['latitude']
            event.longitude = ev['longitude']
            event.start_time = parse_datetime(ev['start_time'])
            event.end_time = parse_datetime(ev['end_time']) if ev['end_time'] else None
            if 'context' in ev:  # API-018; older snapshots keep the current context
                event.context = ev['context']
            event.save()

            owner = event.user
            keep_loc_ids = {loc['id'] for loc in data['locations']}
            event.location_set.exclude(id__in=keep_loc_ids).delete()
            id_map = {}
            for loc in data['locations']:
                fields = {k: v for k, v in loc.items() if k != 'id'}
                obj = Location.objects.filter(pk=loc['id'], event=event).first()
                if obj is None:
                    obj = Location(event=event, user=owner)
                for k, v in fields.items():
                    setattr(obj, k, v)
                obj.save()
                id_map[loc['id']] = obj.id

            keep_item_ids = {i['id'] for i in data['schedule']}
            event.scheduleditem_set.exclude(id__in=keep_item_ids).delete()
            for it in data['schedule']:
                obj = ScheduledItem.objects.filter(pk=it['id'], event=event).first()
                if obj is None:
                    obj = ScheduledItem(event=event, user=owner)
                obj.name = it['name']
                obj.description = it['description']
                obj.start_time = parse_datetime(it['start_time'])
                obj.end_time = parse_datetime(it['end_time']) if it['end_time'] else None
                obj.location_id = id_map.get(it['location_id'])
                obj.tags = it['tags']
                if 'context' in it:
                    obj.context = it['context']
                obj.hidden = it['hidden']
                obj.locked_fields = it['locked_fields']
                obj.save()
            if data.get('tickets') and settings.TICKETS_ENABLED:  # parked: older snapshots' ticket types stay put
                from tickets.content import restore as restore_tickets
                restore_tickets(event, data['tickets'])
    return event


def mark_locked(obj, changed_fields):
    """Record fields a person edited so AI refreshes leave them alone."""
    managed = set(type(obj).CONTENT_FIELDS)
    locked = list(obj.locked_fields or [])
    for f in changed_fields:
        if f in managed and f not in locked:
            locked.append(f)
    obj.locked_fields = locked
