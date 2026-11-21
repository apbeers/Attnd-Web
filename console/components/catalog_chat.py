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

    def add(key, state, text, url='', link=''):
        out.append({'key': key, 'state': state, 'text': text, 'url': url, 'link': link})

    waiting = ChangeSet.objects.filter(event=event, status=ChangeSet.PENDING).count()
    if waiting:
        add('review', PROBLEM, f'{waiting} AI update{"s" if waiting != 1 else ""} waiting for your review',
            reverse('console:changesets', args=[pk]), 'Review')
    locations = list(event.location_set.filter(hidden=False).only('id', 'name', 'latitude', 'longitude', 'image',
                                                                  'image_url'))
    items = list(event.scheduleditem_set.filter(hidden=False).only('id', 'name', 'start_time', 'end_time',
                                                                   'location_id'))
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
           'when the chat opens, and for "what should I do?".', ids=(),
           chat_only='The chat\'s opening briefing.')
def attention(user):
    from django.db.models import Q

    from events.models import Event, EventMember
    now = timezone.now()
    mine = Event.objects.filter(Q(user=user) | Q(id__in=EventMember.objects.filter(user=user).values('event_id')))
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
