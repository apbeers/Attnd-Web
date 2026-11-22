"""Components for the chat-first console (stage I): what needs attention, and whether an event is ready.

`checks(event)` is the one list of rules both use: AI suggestions waiting, notifications scheduled, places without a
pin or a photo, overlapping or out-of-dates schedule items, empty assistant details, a draft that starts soon,
nothing to show yet. Each check links to the page (or canvas) where it is fixed; publishing is the registered
always-ask action.
"""

from collections import defaultdict
from datetime import timedelta

from django.urls import reverse
from django.utils import timezone

from console.components import component
from console.components.catalog import _tz, editable_event, fmt

PROBLEM, INFO, OK = 'problem', 'info', 'ok'


def _overlaps(items):
    """Pairs of items at the same place whose times overlap (an end missing = 1 hour)."""
    by_place = defaultdict(list)
    for it in items:
        if it.location_id:
            by_place[it.location_id].append(it)
    pairs = []
    for rows in by_place.values():
        rows.sort(key=lambda it: it.start_time)
        for a, b in zip(rows, rows[1:], strict=False):
            end = a.end_time or a.start_time + timedelta(hours=1)
            if b.start_time < end:
                pairs.append((a, b))
    return pairs


def checks(event, now=None):
    """[{key, state, text, url, link}] for one event, problems first."""
    from events.models import Notification
    from scrape.models import ChangeSet
    now = now or timezone.now()
    pk = event.pk
    out = []

    def add(key, state, text, url='', link='', post=False):
        out.append({'key': key, 'state': state, 'text': text, 'url': url, 'link': link, 'post': post})

    waiting = ChangeSet.objects.filter(event=event, status=ChangeSet.PENDING).count()
    if waiting:
        add('review', PROBLEM, f'{waiting} AI update{"s" if waiting != 1 else ""} waiting for your review',
            reverse('console:changesets', args=[pk]), 'Review')
    locations = list(event.location_set.filter(hidden=False).only('id', 'name', 'latitude', 'longitude', 'image',
                                                                  'image_url', 'context'))
    items = list(event.scheduleditem_set.filter(hidden=False).only('id', 'name', 'start_time', 'end_time',
                                                                   'location_id', 'context'))
    if not locations:
        add('locations', PROBLEM, 'No locations yet', reverse('console:location_new', args=[pk]), 'Add one')
    if not items:
        add('schedule', PROBLEM, 'No schedule yet', reverse('console:item_new', args=[pk]), 'Add one')
    no_pin = [loc for loc in locations if not loc.has_coordinates]
    if no_pin:
        add('pins', PROBLEM, f'{len(no_pin)} place{"s" if len(no_pin) != 1 else ""} without a map pin'
            + (f' ({", ".join(loc.name for loc in no_pin[:3])}{"…" if len(no_pin) > 3 else ""})'),
            reverse('console:location_edit', args=[pk, no_pin[0].pk]), 'Set the pin')
    elif locations:
        add('pins', OK, 'Every place has a map pin')
    no_image = [loc for loc in locations if not loc.image and not loc.image_url]
    if no_image and locations:
        add('photos', INFO, f'{len(no_image)} place{"s" if len(no_image) != 1 else ""} without a photo',
            reverse('console:location_edit', args=[pk, no_image[0].pk]), 'Add a photo')
    clashes = _overlaps(items)
    if clashes:
        a, b = clashes[0]
        add('overlaps', PROBLEM, f'{len(clashes)} overlapping set{"s" if len(clashes) != 1 else ""} at the same '
            f'place ({a.name} and {b.name})', reverse('console:item_edit', args=[pk, b.pk]), 'Fix the time')
    start = event.start_time
    end = event.end_time or (start + timedelta(hours=12) if start else None)
    if start and items:
        outside = [it for it in items if it.start_time < start - timedelta(hours=12)
                   or (end and it.start_time > end + timedelta(hours=12))]
        if outside:
            add('dates', PROBLEM, f'{len(outside)} schedule item{"s" if len(outside) != 1 else ""} outside the '
                f'event\'s dates ({outside[0].name})', reverse('console:item_edit', args=[pk, outside[0].pk]),
                'Fix the date')
    with_details = [it for it in items if any((it.context or {}).values())]
    if items and len(with_details) < len(items) * 0.8:
        add('details', PROBLEM if len(with_details) < len(items) / 2 else INFO,
            f'Details on {len(with_details)} of {len(items)} schedule item{"s" if len(items) != 1 else ""} '
            '(performers, prices, links: attendees\' phones answer from them)',
            reverse('console:event_fill_details', args=[pk]) if event.source_urls else
            reverse('console:schedule', args=[pk]), 'Fill in details' if event.source_urls else 'Add them',
            post=bool(event.source_urls))
    places_with = [loc for loc in locations if any((loc.context or {}).values())]
    if len(locations) >= 3 and len(places_with) < len(locations) / 2:
        add('place_details', INFO, f'Details on {len(places_with)} of {len(locations)} places (menus, hours, links)',
            reverse('console:event_fill_details', args=[pk]) if event.source_urls else
            reverse('console:locations', args=[pk]), 'Fill in details' if event.source_urls else 'Add them',
            post=bool(event.source_urls))
    if not (event.context or {}).get('about'):
        add('assistant', INFO, 'Details for the assistant are empty (attendees\' phones answer questions from them)',
            reverse('console:event_edit', args=[pk]), 'Add details')
    scheduled = Notification.objects.filter(event=event, sent=False, send_time__gt=now).order_by('send_time')
    count = scheduled.count()
    if count:
        with timezone.override(_tz(event)):
            when = fmt(scheduled[0].send_time, 'D g:i A')
        add('scheduled', INFO, f'{count} notification{"s" if count != 1 else ""} scheduled, next {when}',
            reverse('console:notifications', args=[pk]), 'See them')
    if event.status == 'draft':
        soon = start and start - now < timedelta(days=7)
        add('publish', PROBLEM if soon else INFO,
            'Not published yet' + (f', and it starts {fmt(start, "D M j")}' if soon and start > now else ''),
            reverse('console:event', args=[pk]), '')
    order = {PROBLEM: 0, INFO: 1, OK: 2}
    return sorted(out, key=lambda c: order[c['state']])


def score(found):
    """0–100: the share of the event's checks that pass (problems count fully, notes half)."""
    weights = {'review': 2, 'locations': 3, 'schedule': 3, 'pins': 2, 'overlaps': 2, 'dates': 2, 'publish': 1,
               'assistant': 1, 'photos': 1}
    total = sum(weights.values())
    lost = sum(weights.get(c['key'], 0) * (1 if c['state'] == PROBLEM else 0.5)
               for c in found if c['state'] != OK and c['key'] in weights)
    return max(0, round(100 * (total - lost) / total))


@component('event_readiness', 'Ready to publish?', 'Is the event ready: a score and the checks (places, pins, '
           'photos, overlapping or out-of-dates sets, AI updates waiting, assistant details, publishing), each with '
           'its fix.', actions=(('publish_event', 'Publish'),),
           page=lambda ids: reverse('console:event', args=[ids['event_id']]),
           chat_only='The chat\'s and canvas Overview\'s readiness check (the Overview page shows the same numbers).')
def event_readiness(user, event_id=None, event=None):
    event = editable_event(user, event_id, event)
    found = checks(event)
    problems = [c for c in found if c['state'] == PROBLEM]
    actions = [{'name': 'publish_event', 'style': 'primary'}] if event.status == 'draft' and not any(
        c['key'] in ('locations', 'schedule') for c in problems) else []
    return {'event_id': event.pk, 'name': event.name, 'score': score(found), 'checks': found,
            'problems': len(problems), 'draft': event.status == 'draft', 'actions': actions}


@component('attention', 'What needs attention', 'A briefing across the person\'s current events: AI updates '
           'waiting, scheduled notifications, places without a pin, overlapping sets, drafts starting soon. Shown '
           'when the chat opens, and for "what should I do?".', ids=(), permission='signed_in',
           chat_only='The chat\'s opening briefing.')
def attention(user):
    from events.models import Event
    now = timezone.now()
    mine = Event.objects.for_user(user)
    events = [e for e in mine.exclude(status='archived').order_by('start_time')
              if not e.start_time or (e.end_time or e.start_time + timedelta(hours=12)) > now - timedelta(days=1)][:6]
    rows = []
    for event in events:
        for check in checks(event, now):
            if check['state'] == OK or (check['state'] == INFO and check['key'] not in ('scheduled', 'publish')):
                continue
            rows.append({**check, 'event': event.name, 'event_id': event.pk})
    rows.sort(key=lambda r: 0 if r['state'] == PROBLEM else 1)
    return {'rows': rows[:8], 'more': max(0, len(rows) - 8), 'events': len(events)}


def _hourly(event):
    """(day label, [{hour, label, count, pct}], busiest) for the latest event day with app activity, in its zone."""
    from django.db.models import Sum

    from console.usage import INTERACTIONS
    from events.models import UsageCount
    tz = _tz(event)
    rows = (UsageCount.objects.filter(event=event, metric__in=INTERACTIONS).values_list('bucket')
            .annotate(n=Sum('count')).values_list('bucket', 'n'))
    by_day = defaultdict(lambda: defaultdict(int))
    for bucket, n in rows:
        local = bucket.astimezone(tz)
        by_day[local.date()][local.hour] += n
    if not by_day:
        return '', [], None
    day = max(by_day)
    hours = by_day[day]
    first, last = min(hours), max(hours)
    peak = max(hours.values()) or 1
    bars = []
    for h in range(first, last + 1):
        label = f'{(h % 12) or 12} {"AM" if h < 12 else "PM"}'
        pct = round(100 * hours.get(h, 0) / peak)
        height = max(1, round(pct * 0.56)) if hours.get(h) else 0
        i = h - first
        bars.append({'hour': h, 'label': label, 'count': hours.get(h, 0), 'pct': pct,
                     'x': i * 10 + 1, 'y': 58 - height, 'h': height})
    best = max(bars, key=lambda b: b['count'])
    return day.strftime('%a %b %-d'), bars, best


@component('usage_answer', 'Numbers', 'The event\'s numbers for questions like "how many people liked the taco '
           'trucks?" or "busiest hour yesterday?": activity per hour on the latest day with data (a bar chart, the '
           'busiest hour), joins, and the most liked places and items with their likes.',
           page=lambda ids: reverse('console:usage', args=[ids['event_id']]),
           chat_only='The chat\'s and canvas Metrics tab\'s answer card (the Usage page has the full charts).')
def usage_answer(user, event_id=None, event=None):
    from console.usage import net_hearts
    from events.models import Location, ScheduledItem, UsageCount
    event = editable_event(user, event_id, event)
    day, bars, best = _hourly(event)
    hearts = net_hearts(event)
    liked = []
    for kind, model in (('place', Location), ('item', ScheduledItem)):
        names = dict(model.objects.filter(event=event, pk__in=hearts[kind if kind == 'item' else 'location'])
                     .values_list('id', 'name'))
        for pk, n in hearts['item' if kind == 'item' else 'location'].items():
            if pk in names:
                liked.append({'kind': kind, 'name': names[pk], 'likes': n})
    liked.sort(key=lambda r: (-r['likes'], r['name']))
    from django.db.models import Sum
    joins = UsageCount.objects.filter(event=event, metric='join').aggregate(n=Sum('count'))['n'] or 0
    return {'event_id': event.pk, 'name': event.name, 'day': day, 'bars': bars, 'width': len(bars) * 10,
            'first': bars[0]['label'] if bars else '', 'last': bars[-1]['label'] if bars else '',
            'busiest': {'label': best['label'], 'count': best['count']} if best and best['count'] else None,
            'joins': joins, 'liked': liked[:8], 'has_data': bool(bars or liked or joins)}


@component('event_rollover', 'Copy or roll over', 'A copy of the event as a new draft on a new first day (default: '
           'the same weekday next year): the form with the name and date, and a preview of the first and last '
           'schedule items at their new times. Creating it is a normal save (the copy is a draft).',
           actions=(('duplicate_event', 'Make the copy'),),
           page=lambda ids: reverse('console:event', args=[ids['event_id']]),
           chat_only='The chat\'s and canvas Overview\'s copy card (the overview page has no copy form).')
def event_rollover(user, event_id=None, event=None, form=None):
    from console import rollover
    from console.components import form_json
    from console.forms.event_forms import DuplicateEventForm
    event = editable_event(user, event_id, event)
    tz = _tz(event)
    shift = rollover.default_shift(event)
    start = timezone.localtime(event.start_time + shift, tz)
    year = start.year
    name = event.name.replace(str(year - 1), str(year)) if str(year - 1) in event.name else f'{event.name} {year}'
    form = form or DuplicateEventForm(initial={'name': name, 'start_date': start.date(), 'keep_team': True})
    items = list(event.scheduleditem_set.order_by('start_time')[:1]) + list(
        event.scheduleditem_set.order_by('-start_time')[:1])
    preview = []
    for it in items[:2] if len(items) == 2 and items[0].pk != items[1].pk else items[:1]:
        with timezone.override(tz):
            preview.append({'name': it.name, 'old': fmt(it.start_time, 'D M j, Y g:i A'),
                            'new': fmt(it.start_time + shift, 'D M j, Y g:i A')})
    data = form_json(form, ['name', 'start_date', 'keep_team'], action=reverse('console:event_duplicate',
                                                                                 args=[event.pk]),
                     submit=[{'label': 'Make the copy', 'style': 'primary'}])
    counts = {'locations': event.location_set.count(), 'items': event.scheduleditem_set.count(),
              'team': event.members.count()}
    return {'event_id': event.pk, 'name': event.name, 'form': data, 'preview': preview, 'counts': counts,
            'shift_days': shift.days}


WALK_STEPS = [
    ('import', 'Import', 'Paste your event\'s website or drop a flyer, and the AI builds the event.'),
    ('review', 'Review', 'Check what the AI found: locations, schedule, details.'),
    ('layout', 'Layout', 'Pick the tabs and the look attendees see in the app.'),
    ('publish', 'Publish', 'Publish it so attendees can join.'),
    ('share', 'Share', 'Print the QR code or share the link.'),
]


def walkthrough_state(user):
    """(event or None, {step: done}) for the person's newest event."""
    from events.models import Event, UsageCount
    from scrape.models import ChangeSet
    event = (Event.objects.for_user(user)
             .order_by('-created_at').first())
    done = {key: False for key, _, _ in WALK_STEPS}
    if event is None:
        return None, done
    done['import'] = event.location_set.exists() or event.scheduleditem_set.exists()
    done['review'] = done['import'] and not ChangeSet.objects.filter(event=event, status=ChangeSet.PENDING).exists()
    done['layout'] = bool(event.display_config)
    done['publish'] = event.status == Event.STATUS_PUBLISHED
    done['share'] = done['publish'] and (event.device_set.exists() or UsageCount.objects.filter(
        event=event, metric='link_open').exists())
    return event, done


def show_walkthrough(user):
    """Open the chat on the walkthrough? Only until it is finished or skipped, and for people with ≤ 2 events."""
    from console.models import ConsolePreference
    from events.models import Event
    if ConsolePreference.objects.filter(user=user, walkthrough_done=True).exists():
        return False
    count = Event.objects.for_user(user).count()
    return count <= 2


@component('first_run', 'Get started', 'The first-run walkthrough for a new organizer: import → review → layout → '
           'publish → share as progress chips, the current step with its action; skippable, shown once.', ids=(),
           permission='signed_in',
           chat_only='The chat\'s opening card for new organizers.')
def first_run(user):
    from console.models import ConsolePreference
    if not show_walkthrough(user):  # finished, skipped, or not a new organizer: the chat shows the briefing instead
        return {'show': False, 'steps': [], 'current': None, 'link_url': '', 'event': '', 'done_count': 0,
                'skip_url': ''}
    event, done = walkthrough_state(user)
    steps = []
    current = next((key for key, _, _ in WALK_STEPS if not done[key]), None)
    for key, label, text in WALK_STEPS:
        steps.append({'key': key, 'label': label, 'text': text, 'done': done[key], 'current': key == current})
    if current is None and event is not None:  # all done: never again
        ConsolePreference.objects.update_or_create(user=user, defaults={'walkthrough_done': True})
    links = {}
    if event is not None:
        links = {'review': reverse('console:changesets', args=[event.pk]),
                 'layout': reverse('console:display', args=[event.pk]),
                 'publish': reverse('console:event', args=[event.pk]),
                 'share': reverse('console:event_poster', args=[event.pk])}
    now = next((s for s in steps if s['current']), None)
    return {'show': True, 'steps': steps, 'current': now, 'link_url': links.get(current, ''), 'event': event.name if event else '',
            'done_count': sum(1 for s in steps if s['done']), 'skip_url': reverse('console:walkthrough_skip')}
