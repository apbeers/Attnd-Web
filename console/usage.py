"""Summaries of anonymous usage counts (API-005) for the console's Usage pages."""
from collections import defaultdict
from datetime import timedelta

from django.db.models import Count, Sum
from django.utils import timezone

from events.models import Device, Event, Location, Notification, ScheduledItem, UsageCount

TAB_LABELS = {'map': 'Map', 'locations': 'Locations', 'schedule': 'Schedule', 'flyer': 'Flyer',
              'notifications': 'Updates', 'options': 'Options'}
INTERACTIONS = ('app_open', 'tab_view', 'location_view', 'schedule_item_view', 'notification_open', 'search', 'refresh')

CHART_W, CHART_H = 720, 160


def chart(points, tz, *, max_bars=72):
    """Bars for an inline SVG: hourly when the data spans up to `max_bars` hours, otherwise daily.

    `points` maps a UTC hour to a value. Hours step in UTC (so daylight-saving changes don't skip or
    repeat bars) and are labelled in the event's time zone; days are the event's local days.
    Returns {'bars': [...], 'unit': 'hour'|'day', 'max': n, ...} or None when there's no data.
    """
    if not points:
        return None
    first, last = min(points), max(points)
    hourly = (last - first) <= timedelta(hours=max_bars - 1)
    buckets = defaultdict(int)
    for hour, value in points.items():
        buckets[hour if hourly else hour.astimezone(tz).date()] += value
    step = timedelta(hours=1) if hourly else timedelta(days=1)
    keys, cursor, end = [], min(buckets), max(buckets)
    while cursor <= end and len(keys) < 400:
        keys.append(cursor)
        cursor += step
    top = max(buckets.values()) or 1
    width = CHART_W / len(keys)
    bars = []
    for i, key in enumerate(keys):
        value = buckets.get(key, 0)
        height = round(value / top * (CHART_H - 20), 1)
        label = key.astimezone(tz).strftime('%a %-I %p') if hourly else key.strftime('%a %b %-d')
        bars.append({'x': round(i * width + 1, 1), 'w': round(max(width - 2, 1), 1), 'h': height,
                     'y': round(CHART_H - height, 1), 'value': value, 'label': label,
                     'left': round(i / len(keys) * 100, 2),
                     'tick': i % max(1, len(keys) // 6) == 0})
    return {'bars': bars, 'unit': 'hour' if hourly else 'day', 'max': top, 'width': CHART_W, 'height': CHART_H}


def _named(event_rows, model, event):
    names = dict(model.objects.filter(event=event).values_list('id', 'name' if model is not Notification else 'title'))
    out = []
    for detail, count in event_rows:
        try:
            name = names.get(int(detail))
        except ValueError:
            name = None
        if name:
            out.append({'name': name, 'count': count})
    return out


def event_usage(event, tz):
    rows = UsageCount.objects.filter(event=event)
    totals = dict(rows.values_list('metric').annotate(n=Sum('count')).values_list('metric', 'n'))
    by_detail = defaultdict(list)
    for metric, detail, n in (rows.exclude(detail='').values_list('metric', 'detail')
                              .annotate(n=Sum('count')).order_by('-n').values_list('metric', 'detail', 'n')):
        by_detail[metric].append((detail, n))

    activity, joins = defaultdict(int), defaultdict(int)
    for metric, hour, n in rows.values_list('metric', 'hour').annotate(n=Sum('count')).values_list('metric', 'hour', 'n'):
        if metric in INTERACTIONS:
            activity[hour] += n
        elif metric == 'join':
            joins[hour] += n

    platforms = dict(rows.filter(metric__in=INTERACTIONS).values_list('platform').annotate(n=Sum('count'))
                     .values_list('platform', 'n'))
    busiest = max(activity.items(), key=lambda kv: kv[1]) if activity else None
    return {
        'has_data': bool(totals),
        'joins': totals.get('join', 0),
        'leaves': totals.get('leave', 0),
        'opens': totals.get('app_open', 0),
        'interactions': sum(totals.get(m, 0) for m in INTERACTIONS),
        'notification_opens': totals.get('notification_open', 0),
        'searches': totals.get('search', 0),
        'refreshes': totals.get('refresh', 0),
        'tabs': [{'name': TAB_LABELS.get(d, d), 'count': n} for d, n in by_detail['tab_view']],
        'top_locations': _named(by_detail['location_view'], Location, event)[:10],
        'top_items': _named(by_detail['schedule_item_view'], ScheduledItem, event)[:10],
        'top_notifications': _named(by_detail['notification_open'], Notification, event)[:10],
        'platforms': {'ios': platforms.get('ios', 0), 'android': platforms.get('android', 0)},
        'busiest': {'hour': busiest[0].astimezone(tz), 'count': busiest[1]} if busiest else None,
        'activity_chart': chart(activity, tz),
        'joins_chart': chart(joins, tz),
    }


def app_usage(tz, days=30):
    since = timezone.now() - timedelta(days=days)
    rows = UsageCount.objects.filter(hour__gte=since)
    daily = {m: defaultdict(int) for m in ('install', 'join', 'activity')}
    for metric, hour, n in rows.values_list('metric', 'hour').annotate(n=Sum('count')).values_list('metric', 'hour', 'n'):
        key = 'activity' if metric in INTERACTIONS else metric
        if key in daily:
            daily[key][hour] += n
    totals = dict(rows.values_list('metric').annotate(n=Sum('count')).values_list('metric', 'n'))

    per_event = defaultdict(lambda: {'joins': 0, 'activity': 0})
    for event_id, metric, n in (rows.exclude(event=None).values_list('event', 'metric').annotate(n=Sum('count'))
                                .values_list('event', 'metric', 'n')):
        if metric == 'join':
            per_event[event_id]['joins'] += n
        elif metric in INTERACTIONS:
            per_event[event_id]['activity'] += n
    events = {e.id: e for e in Event.objects.filter(id__in=per_event)}
    event_rows = sorted(({'event': events[i], **v} for i, v in per_event.items() if i in events),
                        key=lambda r: (r['activity'], r['joins']), reverse=True)[:50]

    seen = Device.objects.filter(last_seen__gte=since)
    versions = (seen.values('platform', 'app_version', 'api_version').annotate(n=Count('id'))
                .order_by('platform', '-n'))
    return {
        'days': days,
        'installs': totals.get('install', 0),
        'joins': totals.get('join', 0),
        'activity': sum(totals.get(m, 0) for m in INTERACTIONS),
        'active_devices': seen.count(),
        'installs_chart': chart(daily['install'], tz, max_bars=0),
        'joins_chart': chart(daily['join'], tz, max_bars=0),
        'activity_chart': chart(daily['activity'], tz, max_bars=0),
        'events': event_rows,
        'versions': list(versions[:40]),
    }


def find_devices(code):
    """Support lookup by the code an app shows in Options (the start of its random device ID)."""
    code = (code or '').strip().replace('-', '')
    if len(code) < 6:
        return []
    candidates = Device.objects.select_related('event').order_by('-last_seen')
    matches = [d for d in candidates.filter(id__istartswith=code[:8])[:50]
               if d.id.replace('-', '').lower().startswith(code.lower())]
    return matches[:10]
