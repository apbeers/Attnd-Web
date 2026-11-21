"""The console's components (see console/components/__init__.py): data functions for the most-used pieces.

Each data function takes either ids (the Ask Attnd chat; loaded and permission-checked here, Http404 like the views)
or objects a console view already loaded for this person (the pages). Everything returned is plain JSON-able data;
the templates in console/templates/components/ draw only from it.
"""

import json
from datetime import timedelta

from django.contrib.humanize.templatetags.humanize import naturaltime
from django.db.models import Count, IntegerField, OuterRef, Q, Subquery
from django.db.models.functions import Coalesce
from django.http import Http404
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils import dateformat, timezone
from django.utils.text import Truncator
from django.utils.timesince import timeuntil

from console.components import component, form_json
from events.content import app_image_url

DRAFT_TITLE = "Only your team can see it; attendees can't join yet"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def fmt(dt, pattern):
    """Like the template filter `date`, in the active time zone."""
    return dateformat.format(timezone.localtime(dt), pattern) if dt else ''


def _tz(event):
    from scrape.diff import tz_or_utc
    return tz_or_utc(event.timezone)


def editable_event(user, event_id=None, event=None):
    """The event, when this person is on its team (like console.views_events.event_view); else Http404."""
    if event is not None:
        return event  # loaded and checked by the page's view
    from events.models import Event
    event = get_object_or_404(Event, pk=event_id)
    if not event.can_edit(user):
        raise Http404
    return event


def badge(label, tone, title='', icon='', live=False):
    return {'label': label, 'tone': tone, 'title': title, 'icon': icon, 'live': live}


def status_badges(e):
    """The status badges of an event (lifecycle, payment, timing, watching, testing, owner), in order."""
    out = []
    if e.status == 'draft':
        out.append(badge('Draft', 'warning', DRAFT_TITLE))
    elif e.status == 'archived':
        out.append(badge('Archived', 'muted', "Attendees see an 'event has ended' banner"))
    else:
        out.append(badge('Live', 'success', 'Published: attendees can join'))
    if e.paid:  # (no "Demo" badge in the console any more, owner Nov 21, 2026: unpaid events show their free spots)
        out.append(badge('Paid', 'neutral', 'Paid: going live, no phone limit'))
    timing = e.timing
    if timing == 'now' and e.status != 'archived':
        out.append(badge('Happening now', 'live', 'The event is on right now', live=True))
    elif timing == 'ended' and e.status != 'archived':
        out.append(badge('Ended', 'ended'))
    if e.refresh_schedule == 'watch':
        out.append(badge('Watching website', 'neutral', 'Attnd keeps the app in sync with the event website', 'refresh'))
    if e.testing_only:
        out.append(badge('Testing only', 'warning', "Staff test copy of a prospect's event: published but unlisted "
                                                    '(code / QR only), never watched'))
    if e.user_id is None:
        out.append(badge('Unassigned', 'warning', "The owner's account was deleted"))
    return out


def ScheduledItemModel():
    from events.models import ScheduledItem
    return ScheduledItem


def _count(queryset, field='event'):
    rows = queryset.filter(**{field: OuterRef('pk')}).order_by().values(field).annotate(n=Count('pk')).values('n')
    return Coalesce(Subquery(rows, output_field=IntegerField()), 0)


def annotated_events(user):
    """The person's events with the dashboard's counts (one correlated subquery per number, no joins)."""
    from events.models import Device, Location, ScheduledItem, events_for_user
    from scrape.models import ChangeSet
    return (events_for_user(user)
            .annotate(n_locations=_count(Location.objects.all()),
                      n_items=_count(ScheduledItem.objects.all()),
                      n_devices=_count(Device.objects.attendees()),
                      n_pending=_count(ChangeSet.objects.filter(status=ChangeSet.PENDING)))
            .order_by('-start_time'))


# Your events in sections (owner, Nov 21, 2026): the dashboard, the canvas Events tab, the chat and the agent's
# list_events use the same rules. Showcase events and DEMO go into a folded last section, for staff only.
SECTIONS = [('now', 'Happening now'), ('upcoming', 'Upcoming'), ('past', 'Past'), ('showcase', 'Showcase & demo')]
SECTION_LABELS = dict(SECTIONS)
PAST_SHOWN = 6  # past events folded after this many ("Show all past events")


def is_showcase(event):
    from django.conf import settings
    code = (getattr(settings, 'DEMO_EVENT_CODE', '') or '').upper()
    return bool(event.showcase or (code and (event.event_code or '').upper() == code))


def event_section(event, user=None):
    """'now', 'upcoming', 'past' or (staff only) 'showcase', by the event's own start and end."""
    if user is not None and (user.is_staff or user.is_superuser) and is_showcase(event):
        return 'showcase'
    if not event.start_time:
        return 'upcoming'
    return {'now': 'now', 'ended': 'past'}.get(event.timing, 'upcoming')


def sectioned(events, user=None):
    """[(section, [events])] in section order, empty sections left out: Happening now and Upcoming soonest first,
    Past most recent first, Showcase & demo by name."""
    groups = {key: [] for key, _ in SECTIONS}
    for e in events:
        groups[event_section(e, user)].append(e)
    far = timezone.now() + timedelta(days=36500)
    groups['now'].sort(key=lambda e: e.start_time or far)
    groups['upcoming'].sort(key=lambda e: e.start_time or far)
    groups['past'].sort(key=lambda e: e.effective_end, reverse=True)
    groups['showcase'].sort(key=lambda e: e.name.lower())
    return [(key, groups[key]) for key, _ in SECTIONS if groups[key]]


def upcoming_and_past(events):
    """(kept for older callers) upcoming and live / past."""
    parts = dict(sectioned(events))
    return parts.get('now', []) + parts.get('upcoming', []), parts.get('past', [])


def event_url(ids):
    return reverse('console:event', args=[ids['event_id']])


# ---------------------------------------------------------------------------
# Your events
# ---------------------------------------------------------------------------

def _expand_events(user, **ids):
    return [{'event_id': e.pk, 'event': e} for _, events in sectioned(list(annotated_events(user)), user)
            for e in events]


@component('event_tile', 'Event', 'One of your events: dates, code, status, counts (the console home list). Without '
           'an event_id: all your events.', detail='event_overview', expand=_expand_events, page=event_url)
def event_tile(user, event_id=None, event=None):
    if event is None or not hasattr(event, 'n_locations'):
        event = annotated_events(user).filter(pk=event_id or event.pk).first()
        if event is None:
            raise Http404
    section = event_section(event, user)
    return {
        'id': event.pk, 'name': event.name, 'url': reverse('console:event', args=[event.pk]),
        # The Your events section (Happening now / Upcoming / Past / Showcase & demo): canvas and chat group by it.
        'group': section, 'section': SECTION_LABELS[section],
        'start': fmt(event.start_time, 'D M j, Y') if event.start_time else 'No date yet', 'code': event.event_code,
        'badges': status_badges(event),
        'counts': {'locations': event.n_locations, 'items': event.n_items, 'joined': event.n_devices,
                   'to_review': event.n_pending},
    }


# ---------------------------------------------------------------------------
# Event overview
# ---------------------------------------------------------------------------

@component('event_header', 'Event header', "An event's name, code and status badges, with Publish / Unpublish for "
           'the owner and admins.', actions=(('publish_event', 'Publish'), ('unpublish_event', 'Move back to draft'),
                                             ('list_event_nearby', 'Show in nearby events'),
                                             ('unlist_event', 'Hide from nearby events')),
           detail='event_overview', page=event_url)
def event_header(user, event_id=None, event=None):
    event = editable_event(user, event_id, event)
    actions = []
    if event.can_manage_team(user):
        if event.status != 'published':
            actions.append({'name': 'publish_event', 'label': 'Make live again' if event.status == 'archived'
                            else 'Publish', 'style': 'success', 'icon': 'broadcast'})
        else:
            if event.public:
                actions.append({'name': 'unlist_event'})
            else:
                actions.append(nearby_action(user, event))
            actions.append({'name': 'unpublish_event', 'style': 'ghost'})
    return {'id': event.pk, 'name': event.name, 'code': event.event_code, 'badges': status_badges(event),
            'public': event.public, 'url': reverse('console:event', args=[event.pk]), 'actions': actions}


def nearby_action(user, event, **extra):
    """List in nearby events: only for paid events (API-025: going live is invite-only); an unpaid event shows the
    button disabled with "Paid events only · Remove the limit", never a button that can't be acted on."""
    if event.paid or user.is_staff or user.is_superuser:
        return {'name': 'list_event_nearby', **extra}
    return {'name': 'list_event_nearby', **extra, 'disabled': True, 'note': 'Paid events only',
            'note_link': {'label': 'Remove the limit', 'url': reverse('console:event', args=[event.pk]) + '#free-limit'}}


def _audience(event):
    from events.models import Device
    return Device.objects.attendees().filter(event=event).aggregate(  # one query instead of five
        joined=Count('pk'),
        subscribed=Count('pk', filter=Q(subscribed=True) & ~Q(push_token='')),
        active_today=Count('pk', filter=Q(last_seen__gte=timezone.now() - timedelta(hours=24))),
        ios=Count('pk', filter=Q(platform='ios')),
        android=Count('pk', filter=Q(platform='android')),
    )


@component('event_stats', 'Event numbers', "When the event is on, its locations, schedule items, attendees joined, "
           'active today, with notifications, iPhone/Android, and the most liked places and items.', page=event_url)
def event_stats(user, event_id=None, event=None):
    from console.usage import most_hearted
    from events.display import event_stats as content_stats
    event = editable_event(user, event_id, event)
    with timezone.override(_tz(event)):
        stats, audience = content_stats(event), _audience(event)
        timing = event.timing
        if timing == 'now':
            when = {'state': 'now', 'label': 'Happening now', 'detail': f'ends {fmt(event.effective_end, "D M j, g:i A")}'}
        elif timing == 'upcoming':
            when = {'state': 'upcoming', 'label': f'Starts in {timeuntil(event.start_time)}',
                    'detail': fmt(event.start_time, 'D M j, g:i A')}
        else:
            when = {'state': 'ended', 'label': f'Ended {naturaltime(event.effective_end)}', 'detail': ''}
        liked = []
        for row in most_hearted(event):
            obj = row['obj']
            if row['kind'] == 'location':
                meta = obj.category or 'Location'
                url = reverse('console:location_edit', args=[event.pk, obj.pk])
            else:
                meta = fmt(obj.start_time, 'D g:i A') + (f' · {obj.location.name}' if obj.location else '')
                url = reverse('console:item_edit', args=[event.pk, obj.pk])
            liked.append({'kind': row['kind'], 'id': obj.pk, 'name': obj.name, 'meta': meta, 'likes': row['count'],
                          'url': url})
    return {
        'timing': when,
        'numbers': [
            {'value': stats['location_count'], 'label': 'Locations'},
            {'value': stats['item_count'], 'label': 'Schedule items'},
            {'value': audience['joined'], 'label': 'Attendees joined'},
            {'value': audience['active_today'], 'label': 'Active today'},
            {'value': audience['subscribed'], 'label': 'Get notifications'},
            {'value': f'{audience["ios"]}/{audience["android"]}', 'label': 'iPhone / Android'},
        ],
        'most_liked': liked, 'usage_url': reverse('console:usage', args=[event.pk]),
    }


@component('live_on_phone', 'Live on your phone', "The event's QR code, code and join link (open it in the Attnd "
           'app), with the QR download and the poster; the Preview on my phone QR (that phone sees every change at '
           'once and isn\'t counted as an attendee); how many phones have the latest version.', page=event_url,
           actions=(('remove_preview_phone', 'Remove'),))
def live_on_phone(user, event_id=None, event=None):
    from attnd.links import join_url, open_in_app_links
    from events import preview
    event = editable_event(user, event_id, event)
    qr = reverse('console:event_qr', args=[event.pk])
    phones = preview.phones(event)
    for p in phones['previews']:
        p['actions'] = [{'name': 'remove_preview_phone', 'style': 'ghost', 'fields': {'device': p['id']},
                         'confirm_text': 'Remove this preview phone? It leaves the event.'}]
    actions = [a for p in phones['previews'] for a in p['actions']]
    return {'name': event.name, 'code': event.event_code, 'qr_url': qr, 'qr_download_url': f'{qr}?download=1',
            'poster_url': reverse('console:event_poster', args=[event.pk]), 'join_url': join_url(event),
            'open_app': open_in_app_links(event), 'draft': event.status == 'draft',
            'preview_qr_url': reverse('console:event_preview_qr', args=[event.pk]),
            'preview_open': preview.open_links(event, user), 'phones': phones,
            'phones_url': reverse('console:event_phones', args=[event.pk]), 'actions': actions}


@component('needs_review', 'AI updates to review', 'How many AI updates of an event wait for review, and the latest '
           'ones.', page=lambda ids: reverse('console:changesets', args=[ids['event_id']]))
def needs_review(user, event_id=None, event=None):
    from scrape.models import ChangeSet
    event = editable_event(user, event_id, event)
    pending = list(event.changesets.filter(status=ChangeSet.PENDING).order_by('-created_at')[:5])
    count = len(pending) if len(pending) < 5 else event.changesets.filter(status=ChangeSet.PENDING).count()
    return {'pending': count, 'review_url': reverse('console:changesets', args=[event.pk]),
            'latest': [{'id': cs.pk, 'summary': cs.summary or 'AI update',
                        'url': reverse('console:changeset', args=[cs.pk])} for cs in pending]}


@component('event_map', 'Event map', "A map of every location of an event that has a pin; tap a pin for its "
           'details and View location.', page=lambda ids: reverse('console:locations', args=[ids['event_id']]))
def event_map(user, event_id=None, event=None):
    event = editable_event(user, event_id, event)
    pins, missing = [], 0
    for loc in event.location_set.order_by('sort_order', 'name'):
        if not loc.has_coordinates:
            missing += 1
            continue
        pins.append({'id': loc.pk, 'name': loc.name, 'lat': float(loc.latitude), 'lng': float(loc.longitude),
                     'category': loc.category, 'snippet': Truncator(loc.snippet or loc.address).chars(80),
                     'hidden': loc.hidden, 'url': reverse('console:location_edit', args=[event.pk, loc.pk])})
    center = [float(event.latitude), float(event.longitude)] if event.latitude is not None and event.longitude is not None \
        else ([pins[0]['lat'], pins[0]['lng']] if pins else [39.8, -98.6])
    return {'event_id': event.pk, 'name': f'Map · {event.name}', 'pins': json.dumps(pins), 'count': len(pins),
            'missing': missing, 'center': json.dumps(center), 'zoom': 15 if pins else 4,
            'locations_url': reverse('console:locations', args=[event.pk])}


@component('event_overview', 'Event overview', "An event at a glance: header, AI updates to review, numbers, a map of "
           'its locations, the QR to open it live on your phone, its colour theme.',
           parts=('event_header', 'needs_review', 'event_stats', 'event_map', 'live_on_phone', 'event_theme'),
           page=event_url,
           chat_only="A composite of the Overview page's own parts (header, review, numbers, map, QR, colours).")
def event_overview(user, event_id=None, event=None):
    event = editable_event(user, event_id, event)
    from console.components import get
    parts = []
    for name in ('event_header', 'needs_review', 'event_stats', 'event_map', 'live_on_phone', 'event_theme'):
        data = get(name).data(user, event=event)
        parts.append({**data, 'component': name, 'ids': {'event_id': event.pk}})  # (finish() resolves them)
    return {'id': event.pk, 'name': event.name, 'parts': parts}


# ---------------------------------------------------------------------------
# Locations
# ---------------------------------------------------------------------------

def _location(user, event_id, location_id, event=None, location=None):
    from events.models import Location
    event = editable_event(user, event_id, event)
    if location is None:
        location = get_object_or_404(Location, pk=location_id, event=event)
    return event, location


def _expand_locations(user, event_id=None, **_):
    from console.usage import net_hearts
    event = editable_event(user, event_id)
    hearts = net_hearts(event)['location']
    locs = event.location_set.annotate(n_items=_count(ScheduledItemModel().objects.all(), 'location'))
    return [{'event_id': event.pk, 'location_id': loc.pk, 'event': event, 'location': loc,
             'hearts': hearts.get(loc.pk, 0)} for loc in locs.order_by('sort_order', 'name')]


@component('location_row', 'Location', 'A location (stage, booth, vendor…) of an event: category, summary, tags, '
           'map pin, likes, schedule items; Hide / Show and Delete. With only event_id: every location.',
           ids=('event_id', 'location_id'), detail='location_form', expand=_expand_locations,
           actions=(('toggle_location_hidden', 'Hide'), ('delete_location', 'Delete')),
           page=lambda ids: reverse('console:location_edit', args=[ids['event_id'], ids['location_id']]))
def location_row(user, event_id=None, location_id=None, event=None, location=None, hearts=None):
    event, loc = _location(user, event_id, location_id, event, location)
    if hearts is None:
        from console.usage import net_hearts
        hearts = getattr(loc, 'hearts', None)
        hearts = net_hearts(event)['location'].get(loc.pk, 0) if hearts is None else hearts
    items = loc.n_items if hasattr(loc, 'n_items') else loc.scheduleditem_set.count()
    return {
        'id': loc.pk, 'name': loc.name, 'url': reverse('console:location_edit', args=[event.pk, loc.pk]),
        'image_url': app_image_url(loc.resolved_image_url, 'thumb') or '', 'image_src': loc.resolved_image_url or '', 'category': loc.category, 'hidden': loc.hidden,
        'locked': bool(loc.locked_fields), 'summary': Truncator(loc.snippet or loc.address).chars(90),
        'tags': list(loc.tags or []), 'has_pin': loc.has_coordinates, 'likes': hearts, 'items': items,
        'actions': [{'name': 'toggle_location_hidden', 'label': 'Show' if loc.hidden else 'Hide'},
                    {'name': 'delete_location', 'style': 'danger'}],
    }


LOCATION_CATEGORIES = ['Stage', 'Food', 'Vendor', 'Venue', 'Restroom', 'Parking', 'Info']


def location_form_for(user, event, location=None, data=None, files=None):
    from console.forms.event_forms import LocationForm
    from events.models import Location
    categories = sorted(set(event.location_set.exclude(category='').values_list('category', flat=True)))
    return LocationForm(data, files, instance=location or Location(event=event), categories=categories)


def _event_tags(event, limit=30):
    from scrape.tags import event_vocabulary
    return event_vocabulary(event)[:limit]


def _tags_note(event):
    tags = _event_tags(event)
    return ('note', f'Tags in this event: {", ".join(tags)}', 'tag') if tags else None


def _context_details(form, locked):
    rows = [f.name for f in form.context_fields]
    return ('details', 'Details for the assistant', rows, {
        'open': bool(form.context_errors), 'locked': 'context' in locked, 'css': 'panel assistant-context',
        'intro': 'Attendees’ phones answer questions from these details, even offline. Only add what attendees may '
                 'see.' + (' You’ve edited them, so AI updates only suggest changes.' if 'context' in locked else '')})


def _pin_map(event, loc=None):
    """The location form's map: this place as the main pin, the event's other locations as small muted pins, framed
    to fit (console.js / maps.js read these as data attributes)."""
    others = [{'name': name, 'lat': float(lat), 'lng': float(lng)} for name, lat, lng in
              event.location_set.exclude(pk=getattr(loc, 'pk', None)).exclude(latitude=None).exclude(longitude=None)
              .order_by('sort_order', 'name').values_list('name', 'latitude', 'longitude')[:80]
              if not (abs(lat) < 1e-6 and abs(lng) < 1e-6)]
    if loc is not None and loc.has_coordinates:
        center, zoom = [float(loc.latitude), float(loc.longitude)], 16
    elif event.latitude is not None and event.longitude is not None:
        center, zoom = [float(event.latitude), float(event.longitude)], 15
    else:
        center, zoom = [39.8, -98.6], 4
    return {'center': json.dumps(center), 'zoom': zoom, 'others': json.dumps(others),
            'places_url': reverse('console:place_search', args=[event.pk]),
            'has_pin': bool(loc is not None and loc.has_coordinates)}


def _found_image(loc):
    """Stage D: 'Found automatically · credit' above the image fields when Attnd found the current photo."""
    if not (loc.pk and loc.image_auto and loc.image):
        return None
    return ('block', 'found_image', {'src': app_image_url(loc.image.url, 'preview'), 'raw': loc.image.url, 'credit': loc.image_credit,
                                     'source': loc.image_source_url, 'license': loc.image_license})


@component('location_form', 'Location form', 'Add or edit a location: name, category, summary, description, tags, '
           'address and map pin, website, image, hidden, details for the assistant. Without location_id: a new one.',
           ids=('event_id',), optional_ids=('location_id',),
           actions=(('unlock_location', 'Let AI update it again'), ('find_location_image', 'Find a photo'),
                    ('remove_location_image', 'Remove photo'), ('delete_location', 'Delete')),
           page=lambda ids: (reverse('console:location_edit', args=[ids['event_id'], ids['location_id']])
                             if ids.get('location_id') else reverse('console:location_new', args=[ids['event_id']])))
def location_form(user, event_id=None, location_id=None, event=None, location=None, form=None):
    event = editable_event(user, event_id, event)
    if location is None and location_id:
        _, location = _location(user, event.pk, location_id, event)
    with timezone.override(_tz(event)):
        form = form or location_form_for(user, event, location)
        loc = form.instance
        locked = list(loc.locked_fields or [])
        url = (reverse('console:location_edit', args=[event.pk, loc.pk]) if loc.pk
               else reverse('console:location_new', args=[event.pk]))
        layout = [
            ('split', 'name', 'category'),
            ('block', 'datalist', {'id': 'category-options',
                                   'options': list(getattr(form, 'categories', [])) + LOCATION_CATEGORIES}),
            'snippet', 'description', 'tags_text', _tags_note(event), 'address',
            ('grid', 'latitude', 'longitude'),
            ('block', 'pin_map', _pin_map(event, loc)),
            'website', ('block', 'fill_site', {'url': reverse('console:location_fill', args=[event.pk])}),
            _found_image(loc), ('grid', 'image', 'image_url'), 'hidden', _context_details(form, locked),
        ]
        data = form_json(form, [r for r in layout if r], action=url,
                         submit=[{'label': 'Save', 'style': 'primary'},
                                 {'label': 'Save & add another', 'style': 'outline', 'name': 'save_add', 'value': '1'}],
                         locked=locked)
    actions = []
    if loc.pk:
        if loc.locked_fields:
            actions.append({'name': 'unlock_location', 'style': 'secondary', 'icon': 'unlock'})
        if loc.image_auto and loc.image:
            actions += [{'name': 'find_location_image', 'label': 'Replace photo', 'style': 'outline', 'icon': 'stars'},
                        {'name': 'remove_location_image', 'label': 'Remove photo', 'style': 'outline'}]
        elif not loc.image and not loc.image_url:
            actions.append({'name': 'find_location_image', 'label': 'Find a photo', 'style': 'outline', 'icon': 'stars'})
        actions.append({'name': 'delete_location', 'style': 'danger', 'confirm_text': 'Delete this location?'})
        for a in actions:  # back to this page afterwards, like the page's own buttons
            a['fields'] = {'next': url}
    return {'id': loc.pk, 'event_id': event.pk, 'title': loc.name if loc.pk else 'New location',
            'url': url, 'form': data, 'actions': actions}


# ---------------------------------------------------------------------------
# Schedule
# ---------------------------------------------------------------------------

def _item(user, event_id, item_id, event=None, item=None):
    from events.models import ScheduledItem
    event = editable_event(user, event_id, event)
    if item is None:
        item = get_object_or_404(ScheduledItem.objects.select_related('location'), pk=item_id, event=event)
    return event, item


def _expand_items(user, event_id=None, location_id=None, **_):
    from console.usage import net_hearts
    event = editable_event(user, event_id)
    hearts = net_hearts(event)['item']
    items = event.scheduleditem_set.select_related('location').order_by('start_time', 'name')
    if location_id:
        items = items.filter(location_id=location_id)
    return [{'event_id': event.pk, 'item_id': it.pk, 'event': event, 'item': it, 'hearts': hearts.get(it.pk, 0)}
            for it in items]


@component('schedule_item_row', 'Schedule item', 'A schedule item (set, talk, session…): day, time, location, tags, '
           'likes; Hide / Show and Delete. With only event_id: the whole schedule.',
           ids=('event_id', 'item_id'), detail='schedule_item_form', expand=_expand_items,
           actions=(('toggle_schedule_item_hidden', 'Hide'), ('delete_schedule_item', 'Delete')),
           page=lambda ids: reverse('console:item_edit', args=[ids['event_id'], ids['item_id']]))
def schedule_item_row(user, event_id=None, item_id=None, event=None, item=None, hearts=None):
    event, it = _item(user, event_id, item_id, event, item)
    if hearts is None:
        from console.usage import net_hearts
        hearts = net_hearts(event)['item'].get(it.pk, 0)
    with timezone.override(_tz(event)):
        time = fmt(it.start_time, 'g:i A') + (f'–{fmt(it.end_time, "g:i A")}' if it.end_time else '')
        day = fmt(it.start_time, 'l, F j')
    return {
        'id': it.pk, 'name': it.name, 'url': reverse('console:item_edit', args=[event.pk, it.pk]),
        'day': day, 'time': time, 'tags': list(it.tags or []), 'hidden': it.hidden, 'locked': bool(it.locked_fields),
        'location': it.location.name if it.location_id else '', 'likes': hearts,
        'actions': [{'name': 'toggle_schedule_item_hidden', 'label': 'Show' if it.hidden else 'Hide'},
                    {'name': 'delete_schedule_item', 'style': 'danger'}],
    }


def item_form_for(user, event, item=None, data=None, initial=None):
    from console.forms.event_forms import ScheduledItemForm
    from events.models import ScheduledItem
    if item is None and initial is None:
        last = event.scheduleditem_set.order_by('-start_time').first()
        initial = {'start_time': last.start_time if last else event.start_time}
    return ScheduledItemForm(data, instance=item or ScheduledItem(event=event), event=event, initial=initial or {})


@component('schedule_item_form', 'Schedule item form', 'Add or edit a schedule item: name, start, end, location, '
           'description, tags, hidden, details for the assistant. Without item_id: a new one.',
           ids=('event_id',), optional_ids=('item_id',),
           actions=(('unlock_schedule_item', 'Let AI update it again'), ('delete_schedule_item', 'Delete')),
           page=lambda ids: (reverse('console:item_edit', args=[ids['event_id'], ids['item_id']])
                             if ids.get('item_id') else reverse('console:item_new', args=[ids['event_id']])))
def schedule_item_form(user, event_id=None, item_id=None, event=None, item=None, form=None):
    event = editable_event(user, event_id, event)
    if item is None and item_id:
        _, item = _item(user, event.pk, item_id, event)
    with timezone.override(_tz(event)):
        form = form or item_form_for(user, event, item)
        it = form.instance
        locked = list(it.locked_fields or [])
        url = reverse('console:item_edit', args=[event.pk, it.pk]) if it.pk else reverse('console:item_new',
                                                                                           args=[event.pk])
        layout = ['name', ('grid', 'start_time', 'end_time'), 'location', 'description', 'tags_text',
                  _tags_note(event), 'hidden', _context_details(form, locked)]
        data = form_json(form, [r for r in layout if r], action=url,
                         submit=[{'label': 'Save', 'style': 'primary'},
                                 {'label': 'Save & add another', 'style': 'outline', 'name': 'save_add', 'value': '1'}],
                         locked=locked)
    actions = []
    if it.pk:
        if it.locked_fields:
            actions.append({'name': 'unlock_schedule_item', 'style': 'secondary', 'icon': 'unlock'})
        actions.append({'name': 'delete_schedule_item', 'style': 'danger', 'confirm_text': 'Delete this item?'})
    return {'id': it.pk, 'event_id': event.pk, 'title': it.name if it.pk else 'New schedule item', 'url': url,
            'form': data, 'actions': actions}


# ---------------------------------------------------------------------------
# Notifications (updates)
# ---------------------------------------------------------------------------

def _expand_notifications(user, event_id=None, **_):
    event = editable_event(user, event_id)
    # Scheduled first (soonest first), then sent (newest first): the list's two sections.
    scheduled = list(event.notification_set.filter(sent=False).order_by('send_time'))
    sent = list(event.notification_set.filter(sent=True).order_by('-send_time'))
    return [{'event_id': event.pk, 'notification_id': n.pk, 'event': event, 'notification': n} for n in scheduled + sent]


@component('notification_row', 'Notification', 'A notification (push notification) of an event: title, text, urgent, sent '
           '(and how many devices got it) or scheduled; Send now / Delete for scheduled ones. With only event_id: '
           'every notification.', ids=('event_id', 'notification_id'), expand=_expand_notifications,
           actions=(('send_scheduled_notification', 'Send now'), ('edit_scheduled_notification', 'Save'),
                    ('delete_scheduled_notification', 'Delete')),
           page=lambda ids: reverse('console:notifications', args=[ids['event_id']]))
def notification_row(user, event_id=None, notification_id=None, event=None, notification=None):
    from events.models import Notification
    event = editable_event(user, event_id, event)
    n = notification or get_object_or_404(Notification, pk=notification_id, event=event)
    with timezone.override(_tz(event)):
        sent_at = fmt(n.sent_at or n.send_time, 'M j, g:i A') if n.sent else ''
        scheduled = '' if n.sent else fmt(n.send_time, 'M j, g:i A')
        send_local = '' if n.sent or not n.send_time else timezone.localtime(n.send_time).strftime('%Y-%m-%dT%H:%M')
    now = timezone.now()
    relative = ''
    if not n.sent and n.send_time:
        relative = f'in {timeuntil(n.send_time, now, depth=1)}' if n.send_time > now else 'due now'
    edit = None
    if not n.sent:
        edit = {'url': reverse('console:notification_action', args=[event.pk, n.pk]), 'title': n.title, 'body': n.body,
                'send_time': send_local}
    return {
        'id': n.pk, 'title': n.title, 'body': n.body, 'urgent': n.is_urgent, 'sent': n.sent, 'sent_at': sent_at,
        'delivered': n.delivered_count, 'scheduled': scheduled, 'relative': relative, 'edit': edit,
        'group': 'sent' if n.sent else 'scheduled',
        'actions': [] if n.sent else [{'name': 'send_scheduled_notification', 'style': 'outline'},
                                      {'name': 'delete_scheduled_notification', 'style': 'danger'}],
    }


def notification_form_for(event, data=None):
    from console.forms.event_forms import NotificationForm
    from events.models import next_send_slot
    return NotificationForm(data, event=event, initial={'send_time': next_send_slot(timezone.now() + timedelta(hours=1))})


def _notification_bind(user, ids, data):
    event = editable_event(user, ids['event_id'])
    with timezone.override(_tz(event)):
        form = notification_form_for(event, data)
        form.is_valid()
    return form


@component('notification_form', 'Send a notification', 'Write a notification for the attendees (push notification + the '
           "app's Updates tab): title, text, now or scheduled, urgent; how many devices it reaches. Sending always "
           'asks to confirm.', actions=(('send_notification', 'Send'),), form=_notification_bind,
           page=lambda ids: reverse('console:notifications', args=[ids['event_id']]))
def notification_form(user, event_id=None, event=None, form=None):
    from events import push
    from events.models import Device
    event = editable_event(user, event_id, event)
    with timezone.override(_tz(event)):
        form = form or notification_form_for(event)
        devices = Device.objects.real().filter(event=event)
        url = reverse('console:notifications', args=[event.pk])
        data = form_json(form, ['title', 'body', ('choices', 'when'),
                                ('show_if', 'when:later', 'send-time', ['send_time']), 'urgent',
                                ('block', 'urgent_warning'), ('block', 'lock_preview', {'event': event.name})],
                         action=url, submit=[{'label': 'Send', 'style': 'primary', 'icon': 'send'}],
                         confirm_action='send_notification')
    return {
        'reach': devices.filter(subscribed=True).exclude(push_token='').count(),
        'urgent_reach': devices.exclude(push_token='').count(),
        'live_activities': devices.filter(platform='ios').exclude(live_activity_token='').count(),
        'urgent_left': push.urgent_sends_left(event), 'form': data,
        'actions': [{'name': 'send_notification'}],
    }


# ---------------------------------------------------------------------------
# History (console/history.py: LogEntry, the Django way)
# ---------------------------------------------------------------------------

HISTORY_MODELS = (('location_id', 'events.location'), ('item_id', 'events.scheduleditem'),
                  ('notification_id', 'events.notification'))


@component('object_history', 'History', "Who changed what and when: an event's history (every location, schedule "
           'item, notification and team change), or one location / schedule item / notification with its id; field old → new, '
           'Undo while a change can still be undone.', ids=('event_id',),
           optional_ids=('location_id', 'item_id', 'notification_id'),
           page=lambda ids: reverse('console:history', args=[ids['event_id']]))
def object_history(user, event_id=None, location_id=None, item_id=None, notification_id=None, event=None,
                   location=None, item=None, notification=None, limit=None):
    from console import history
    event = editable_event(user, event_id, event)
    location_id = location_id or getattr(location, 'pk', None)
    item_id = item_id or getattr(item, 'pk', None)
    notification_id = notification_id or getattr(notification, 'pk', None)
    model, object_id = None, None
    for key, label in HISTORY_MODELS:
        value = {'location_id': location_id, 'item_id': item_id, 'notification_id': notification_id}[key]
        if value:
            model, object_id = label, value
            break
    with timezone.override(_tz(event)):
        rows = history.timeline(event.pk, user, model=model, object_id=object_id, limit=limit or (12 if model else 50))
    for r in rows:
        r['undo_url'] = reverse('console:undo', args=[r['undo_id']]) if r['undo_id'] else ''
    return {'event_id': event.pk, 'scope': 'item' if model else 'event', 'rows': rows,
            'url': reverse('console:history', args=[event.pk])}


# ---------------------------------------------------------------------------
# Event details and new events (the full-screen chat manages everything without leaving it)
# ---------------------------------------------------------------------------

@component('event_form', 'Event details', "Edit an event's details: name, description, start, end, time zone, "
           'address and map position, flyer, accent colour, logo, details for the assistant.',
           page=lambda ids: reverse('console:event_edit', args=[ids['event_id']]))
def event_form(user, event_id=None, event=None, form=None):
    from console.forms.event_forms import EventForm
    event = editable_event(user, event_id, event)
    with timezone.override(_tz(event)):
        form = form or EventForm(instance=event)
        locked = list(event.locked_fields or [])
        layout = ['name', 'description', ('grid', 'start_time', 'end_time'), 'timezone',
                  ('note', f'Times are in the event’s time zone ({event.timezone}).', 'info'), 'address',
                  ('grid', 'latitude', 'longitude'), 'flyer', ('split', 'logo_url', 'accent_color'),
                  _context_details(form, locked)]
        data = form_json(form, layout, action=reverse('console:event_edit', args=[event.pk]),
                         submit=[{'label': 'Save', 'style': 'primary'}], locked=locked)
    return {'id': event.pk, 'name': event.name, 'title': f'{event.name}: details', 'form': data}


def _new_event_forms(user, ai_form=None, blank_form=None):
    from console.forms.event_forms import AIImportForm, BlankEventForm
    from studio.access import is_staff
    layout = ['urls', 'text', 'upload', 'instructions'] + ([('block', 'studio_import')] if is_staff(user) else [])
    ai = form_json(ai_form or AIImportForm(prefix='ai'), layout,
                   action=reverse('console:event_new'),
                   submit=[{'label': 'Create with AI', 'style': 'primary', 'icon': 'stars'}])
    ai['hidden'] = {'mode': 'ai', 'browser_tz': ''}  # console.js fills the browser's zone
    ai['background'] = 'Reading your event…'  # runs as a background task (the tray shows its steps)
    if is_staff(user):
        ai['hidden'].update({'studio_import_shown': '1'})  # the Studio checkbox ('studio_import' block)
    with timezone.override('America/Chicago'):
        blank = form_json(blank_form or BlankEventForm(prefix='blank'), ['name', ('grid', 'start_time', 'timezone'), 'address'],
                          action=reverse('console:event_new'), submit=[{'label': 'Create empty event',
                                                                        'style': 'outline'}])
    blank['hidden'] = {'mode': 'blank'}
    return ai, blank


@component('event_new_form', 'New event', 'Create an event: from its website(s), pasted text or a flyer with the AI '
           'import (runs in the background, the task tray shows each step), or an empty draft.', ids=(),
           page=lambda ids: reverse('console:event_new'))
def event_new_form(user, ai_form=None, blank_form=None):
    ai, blank = _new_event_forms(user, ai_form, blank_form)
    return {'title': 'New event', 'ai': ai, 'blank': blank}


# ---------------------------------------------------------------------------
# Team and metrics (the full-screen canvas's Team and Metrics tabs, and chat cards)
# ---------------------------------------------------------------------------

def _team_bind(user, ids, data):
    from console.forms.event_forms import TeamInviteForm
    form = TeamInviteForm(data)
    form.is_valid()
    return form


def _row_actions(event, offered):
    """A row's buttons with url / confirm (they are also in the component's own list, which act() checks)."""
    from console.components import get, resolve_actions
    return [dict(a, **{k: v for k, v in r.items() if k != 'name'}) for a, r in
            zip(offered, resolve_actions(get('event_team'), {'event_id': event.pk}, offered))]


@component('event_team', 'Team', "An event's team: the owner, admins and contributors with their roles, and pending "
           'invitations by email; invite someone, change a role, remove someone, cancel an invitation (these always '
           'ask to confirm).', form=_team_bind,
           actions=(('invite_team_member', 'Invite'), ('cancel_team_invite', 'Cancel'), ('remove_team_member', 'Remove'),
                    ('change_team_role', 'Change role'), ('transfer_event_ownership', 'Make owner'),
                    ('leave_event', 'Leave')),
           page=lambda ids: reverse('console:team', args=[ids['event_id']]))
def event_team(user, event_id=None, event=None, form=None):
    from console.accounts import display_name
    from console.forms.event_forms import TeamInviteForm
    from events.models import EventMember
    event = editable_event(user, event_id, event)
    role = event.role_for(user)
    can_manage = role in ('owner', EventMember.ROLE_ADMIN)
    is_owner = role == 'owner'
    actions, members = [], []
    for m in event.members.select_related('user').order_by('role', 'user__email'):
        row = {'id': m.pk, 'email': m.user.email, 'name': display_name(m.user), 'role': m.get_role_display(),
               'you': m.user_id == user.pk, 'actions': []}
        if is_owner:
            other = EventMember.ROLE_CONTRIBUTOR if m.role == EventMember.ROLE_ADMIN else EventMember.ROLE_ADMIN
            row['actions'].append({'name': 'change_team_role', 'style': 'outline',
                                   'label': f'Make {dict(EventMember.ROLE_CHOICES)[other].lower()}',
                                   'fields': {'member': m.pk, 'role': other},
                                   'confirm_text': f'Make {m.user.email} {dict(EventMember.ROLE_CHOICES)[other].lower()}?'})
        if is_owner:
            row['actions'].append({'name': 'transfer_event_ownership', 'label': 'Make owner', 'style': 'ghost',
                                   'fields': {'member': m.pk},
                                   'confirm_text': f'Make {m.user.email} the owner? You stay on the team as an admin.'})
        if is_owner or (can_manage and m.role == EventMember.ROLE_CONTRIBUTOR):
            row['actions'].append({'name': 'remove_team_member', 'label': 'Remove', 'style': 'danger',
                                   'fields': {'member': m.pk}, 'confirm_text': f'Remove {m.user.email}?'})
        elif m.user_id == user.pk:
            row['actions'].append({'name': 'leave_event', 'label': 'Leave', 'style': 'danger',
                                   'confirm_text': 'Leave this event?'})
        row['actions'] = _row_actions(event, row['actions'])
        actions += row['actions']
        members.append(row)
    invites = []
    for i in event.invites.order_by('email'):
        row = {'id': i.pk, 'email': i.email, 'role': i.get_role_display(), 'actions': []}
        if can_manage:
            row['actions'].append({'name': 'cancel_team_invite', 'label': 'Cancel', 'style': 'danger',
                                   'fields': {'invite': i.pk}, 'confirm_text': f'Cancel the invitation for {i.email}?'})
        row['actions'] = _row_actions(event, row['actions'])
        actions += row['actions']
        invites.append(row)
    invite = None
    if can_manage:
        form = form or TeamInviteForm()
        invite = form_json(form, ['email'] + (['role'] if is_owner else []),
                           action=reverse('console:team', args=[event.pk]),
                           submit=[{'label': 'Invite', 'style': 'primary'}], confirm_action='invite_team_member')
        invite['hidden'] = {'action': 'invite'}
        actions.append({'name': 'invite_team_member'})
    return {
        'event_id': event.pk, 'name': f'Team · {event.name}', 'your_role': role,
        'owner': {'email': event.user.email if event.user_id else '', 'name': display_name(event.user) if event.user_id else '',
                  'you': event.user_id == user.pk},
        'members': members, 'invites': invites, 'invite': invite, 'can_manage': can_manage,
        'url': reverse('console:team', args=[event.pk]), 'actions': actions,
    }


@component('event_metrics', 'Metrics', "This event's usage from the apps (anonymous counts): devices joined, app "
           'opens, views, searches, likes and the most liked places and items, the iOS / Android split and how many '
           'devices push notifications reach.', page=lambda ids: reverse('console:usage', args=[ids['event_id']]))
def event_metrics(user, event_id=None, event=None, usage=None):
    from console.usage import event_usage
    from events.models import Device
    event = editable_event(user, event_id, event)
    tz = _tz(event)
    u = usage or event_usage(event, tz)  # the Usage page passes what it already counted
    devices = Device.objects.attendees().filter(event=event)
    by_platform = dict(devices.values_list('platform').annotate(n=Count('id')).values_list('platform', 'n'))
    in_event = sum(by_platform.values())
    split = u['platforms']
    total = (split['ios'] + split['android']) or 0
    return {
        'event_id': event.pk, 'name': f'Metrics · {event.name}', 'has_data': u['has_data'] or in_event > 0,
        'numbers': [
            {'label': 'In the event now', 'value': in_event},
            {'label': 'Joined', 'value': u['joins']},
            {'label': 'App opens', 'value': u['opens']},
            {'label': 'Searches', 'value': u['searches']},
            {'label': 'Likes', 'value': u['net_hearts']},
            {'label': 'Notifications opened', 'value': u['notification_opens']},
            {'label': 'QR / link opens', 'value': u['link_opens']['total']},
        ],
        'platforms': [
            {'label': 'iOS', 'devices': by_platform.get('ios', 0), 'activity': split['ios'],
             'share': round(100 * split['ios'] / total) if total else 0},
            {'label': 'Android', 'devices': by_platform.get('android', 0), 'activity': split['android'],
             'share': round(100 * split['android'] / total) if total else 0},
        ],
        'push': {'reach': devices.filter(subscribed=True).exclude(push_token='').count(),
                 'urgent_reach': devices.exclude(push_token='').count()},
        'views': {'locations': u['top_locations'][:5], 'items': u['top_items'][:5]},
        'liked': {'locations': u['hearted_locations'][:5], 'items': u['hearted_items'][:5]},
        'tabs': u['tabs'][:6],
        'busiest': (f'{u["busiest"]["hour"]:%a %-I %p} · {u["busiest"]["count"]} interactions'
                    if u['busiest'] else ''),
        'url': reverse('console:usage', args=[event.pk]),
    }
