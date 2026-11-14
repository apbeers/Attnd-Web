"""Summaries of anonymous usage counts (API-005) for the console's Usage pages."""
from collections import defaultdict
from datetime import datetime, timedelta, timezone as dt_timezone

from django.db.models import Count, Sum
from django.utils import timezone

from events.models import Device, Event, Location, Notification, ScheduledItem, UsageCount

TAB_LABELS = {'map': 'Map', 'locations': 'Locations', 'schedule': 'Schedule', 'flyer': 'Flyer',
              'notifications': 'Updates', 'options': 'Options'}
INTERACTIONS = ('app_open', 'tab_view', 'location_view', 'schedule_item_view', 'notification_open', 'search', 'refresh')

CHART_W, CHART_H = 720, 170

# (longest span, step, label format). Up to 2 days of data: 5-minute steps; up to 2 weeks: hourly; else daily.
_STEPS = [
    (timedelta(days=2), timedelta(minutes=5), '%a %-I:%M %p'),
    (timedelta(days=14), timedelta(hours=1), '%a %-I %p'),
    (None, timedelta(days=1), '%a %b %-d'),
]


def _floor(dt, step, tz):
    if step >= timedelta(days=1):
        local = dt.astimezone(tz)
        return local.replace(hour=0, minute=0, second=0, microsecond=0)
    seconds = int(step.total_seconds())
    epoch = int(dt.timestamp())
    return datetime.fromtimestamp(epoch - epoch % seconds, tz=dt_timezone.utc)


def line_chart(series, tz, *, start=None, end=None):
    """Lines for an inline SVG (console/_linechart.html).

    `series`: [{'label', 'cls', 'points': {utc datetime: n}, 'cumulative': bool, 'base': n}]. A cumulative
    series shows the running total (starting from `base`); others show the count per step. Every
    series has its own vertical scale, so a running total and a per-5-minute count share one chart.
    """
    stamps = [t for s in series for t in s['points']]
    if not stamps:
        return None
    # Always show some context: at least 6 hours, up to `end` (e.g. now while the event runs).
    start = min([start or min(stamps), min(stamps)])
    end = max([end or max(stamps), max(stamps)])
    if end - start < timedelta(hours=6):
        start = end - timedelta(hours=6)
    span = end - start
    step, fmt = next((st, f) for limit, st, f in _STEPS if limit is None or span <= limit)
    keys, cursor = [], _floor(start, step, tz)
    last = _floor(end, step, tz)
    while cursor <= last and len(keys) < 2000:
        keys.append(cursor)
        cursor = cursor + step
        if step >= timedelta(days=1):
            cursor = cursor.astimezone(tz).replace(hour=0, minute=0, second=0, microsecond=0)
    n = len(keys)
    out = []
    for s in series:
        per_step = defaultdict(int)
        for t, v in s['points'].items():
            per_step[_floor(t, step, tz)] += v
        values, running = [], s.get('base', 0)
        for k in keys:
            running += per_step.get(k, 0)
            values.append(running if s.get('cumulative') else per_step.get(k, 0))
        top = max(values) or 1
        coords, dots = [], []
        for i, v in enumerate(values):
            x = 0 if n == 1 else round(i / (n - 1) * CHART_W, 1)
            y = round(CHART_H - 4 - v / top * (CHART_H - 14), 1)
            coords.append(f'{x},{y}')
            if not s.get('cumulative') and v and sum(1 for value in values if value) <= 40:
                dots.append({'x': x, 'y': y})  # sparse data: mark each point so a single one is visible
        if n == 1:
            coords.append(f'{CHART_W},{coords[0].split(",")[1]}')
        out.append({'label': s['label'], 'cls': s['cls'], 'points': ' '.join(coords), 'dots': dots, 'values': values,
                    'max': max(values), 'last': values[-1], 'cumulative': bool(s.get('cumulative'))})
    every = max(1, n // 6)
    ticks = [{'left': 0 if n == 1 else round(i / (n - 1) * 100, 2), 'label': keys[i].astimezone(tz).strftime(fmt)}
             for i in range(0, n, every)]
    width = CHART_W / max(n, 1)
    hover = [{'x': round(i * width, 1), 'w': round(width, 1),
              'title': keys[i].astimezone(tz).strftime(fmt) + ': ' + ', '.join(f"{s['label']} {s['values'][i]}" for s in out)}
             for i in range(n)]
    step_label = {300: '5 minutes', 3600: 'hour', 86400: 'day'}[int(step.total_seconds())]
    return {'series': out, 'ticks': ticks, 'hover': hover, 'width': CHART_W, 'height': CHART_H, 'step': step_label}


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
    # Charts run up to now while the event is on (quiet stretches show as zero), and stop after it.
    chart_end = min(timezone.now(), event.effective_end + timedelta(days=1))
    totals = dict(rows.values_list('metric').annotate(n=Sum('count')).values_list('metric', 'n'))
    by_detail = defaultdict(list)
    for metric, detail, n in (rows.exclude(detail='').values_list('metric', 'detail')
                              .annotate(n=Sum('count')).order_by('-n').values_list('metric', 'detail', 'n')):
        by_detail[metric].append((detail, n))

    link_opens = defaultdict(int)  # QR code / join link opens, by device kind
    for detail, n in rows.filter(metric='link_open').values_list('detail').annotate(n=Sum('count')).values_list('detail', 'n'):
        link_opens[detail] += n
    opens_series = defaultdict(int)
    activity, joins = defaultdict(int), defaultdict(int)
    for metric, bucket, n in rows.values_list('metric', 'bucket').annotate(n=Sum('count')).values_list('metric', 'bucket', 'n'):
        if metric in INTERACTIONS:
            activity[bucket] += n
        elif metric == 'join':
            joins[bucket] += n
        elif metric == 'link_open':
            opens_series[bucket] += n
    hourly = defaultdict(int)
    for bucket, n in activity.items():
        hourly[bucket.replace(minute=0)] += n

    platforms = dict(rows.filter(metric__in=INTERACTIONS).values_list('platform').annotate(n=Sum('count'))
                     .values_list('platform', 'n'))
    busiest = max(hourly.items(), key=lambda kv: kv[1]) if hourly else None
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
        'activity_chart': line_chart([{'label': 'Interactions', 'cls': 'new', 'points': activity}], tz, end=chart_end),
        'joins_chart': line_chart([
            {'label': 'Total joined', 'cls': 'total', 'points': joins, 'cumulative': True},
            {'label': 'Joined in that time', 'cls': 'new', 'points': joins},
        ], tz, end=chart_end),
        'link_opens': {'total': sum(link_opens.values()), 'ios': link_opens.get('ios', 0),
                       'android': link_opens.get('android', 0), 'desktop': link_opens.get('desktop', 0)},
        'link_chart': line_chart([
            {'label': 'Total QR / link opens', 'cls': 'total', 'points': opens_series, 'cumulative': True},
            {'label': 'Opens in that time', 'cls': 'new', 'points': opens_series},
        ], tz, end=chart_end),
    }


def app_usage(tz, days=30):
    since = timezone.now() - timedelta(days=days)
    rows = UsageCount.objects.filter(bucket__gte=since)
    series = {m: defaultdict(int) for m in ('install', 'join', 'activity')}
    for metric, bucket, n in rows.values_list('metric', 'bucket').annotate(n=Sum('count')).values_list('metric', 'bucket', 'n'):
        key = 'activity' if metric in INTERACTIONS else metric
        if key in series:
            series[key][bucket] += n
    earlier = UsageCount.objects.filter(bucket__lt=since)
    installs_before = earlier.filter(metric='install').aggregate(n=Sum('count'))['n'] or 0
    joins_before = earlier.filter(metric='join').aggregate(n=Sum('count'))['n'] or 0
    totals = dict(rows.values_list('metric').annotate(n=Sum('count')).values_list('metric', 'n'))

    per_event = defaultdict(lambda: {'joins': 0, 'activity': 0, 'link_opens': 0})
    for event_id, metric, n in (rows.exclude(event=None).values_list('event', 'metric').annotate(n=Sum('count'))
                                .values_list('event', 'metric', 'n')):
        if metric == 'join':
            per_event[event_id]['joins'] += n
        elif metric == 'link_open':
            per_event[event_id]['link_opens'] += n
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
        'link_opens': totals.get('link_open', 0),
        'active_devices': seen.count(),
        'installs_chart': line_chart([
            {'label': 'Total installs', 'cls': 'total', 'points': series['install'], 'cumulative': True, 'base': installs_before},
            {'label': 'New installs', 'cls': 'new', 'points': series['install']},
        ], tz, end=timezone.now()),
        'joins_chart': line_chart([
            {'label': 'Total joins', 'cls': 'total', 'points': series['join'], 'cumulative': True, 'base': joins_before},
            {'label': 'Joins in that time', 'cls': 'new', 'points': series['join']},
        ], tz, end=timezone.now()),
        'activity_chart': line_chart([{'label': 'Interactions', 'cls': 'new', 'points': series['activity']}], tz,
                                     end=timezone.now()),
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
