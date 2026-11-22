"""Anonymous usage counting (API-005).

Counts are bucketed per event, UTC 5-minute window, metric, detail and platform. Nothing identifying is
stored: callers pass no device ID, and the ingest endpoint never reads one.
"""
from datetime import timedelta, timezone as dt_timezone

from django.db import connection
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from events.models import UsageCount

TABS = {'home', 'map', 'locations', 'schedule', 'flyer', 'notifications', 'options'}  # home: API-024

# Metrics the apps may send, and what their `detail` may be.
CLIENT_METRICS = {
    'app_open': None,               # the app came to the foreground while in this event
    'tab_view': 'tab',              # detail: one of TABS
    'location_view': 'location',    # detail: location id
    'schedule_item_view': 'item',   # detail: schedule item id
    'notification_open': 'notification',  # a push was tapped; detail: notification id (optional)
    'search': None,                 # a search was run (never the search text)
    'refresh': None,                # pull to refresh
    # Hearts (API-009): the attendee hearted / un-hearted something. Net hearts = hearts - unhearts.
    'location_heart': 'location',        # detail: location id
    'location_unheart': 'location',
    'schedule_item_heart': 'item',       # detail: schedule item id
    'schedule_item_unheart': 'item',
}
# Anonymous performance (API-014): bucketed values, detail = the bucket label. No per-person data.
LAUNCH_BUCKETS = ('<0.5s', '<1s', '<2s', '<4s', '>=4s')
FETCH_BUCKETS = ('<250ms', '<500ms', '<1s', '<2s', '<5s', '>=5s')
PUSH_DELAY_BUCKETS = ('<5s', '<10s', '<15s', '<30s', '<60s', '>=60s')
PLANNER_BUCKETS = ('<100ms', '<250ms', '<500ms', '<1s', '<2s', '>=2s')
RENDER_BUCKETS = ('<250ms', '<500ms', '<1s', '<2s', '<4s', '>=4s')
PERF_METRICS = {
    'perf_launch_cold': LAUNCH_BUCKETS,        # process start → first frame of the event UI
    'perf_launch_warm': LAUNCH_BUCKETS,        # back to the foreground → interactive
    'perf_bundle_fetch': FETCH_BUCKETS,        # GET bundle/ answered 200 (request start → decoded)
    'perf_bundle_304': FETCH_BUCKETS,          # GET bundle/ answered 304
    'perf_api_error': ('4xx', '5xx', 'timeout', 'offline'),  # any API call that failed
    'perf_push_refresh_delay': PUSH_DELAY_BUCKETS,  # content_updated push received → new bundle shown
    'perf_live_start_fail': ('', 'disabled', 'denied', 'limit', 'error'),  # Live Activity / Live Update didn't start
    'perf_planner_ms': PLANNER_BUCKETS,        # one planner run
    'perf_map_first_render': RENDER_BUCKETS,   # Map tab opened → first pins drawn
}
for _name, _buckets in PERF_METRICS.items():
    CLIENT_METRICS[_name] = 'bucket'
HEART_METRICS = {'location_heart': 'location_unheart', 'schedule_item_heart': 'schedule_item_unheart'}
HEART_SET = set(HEART_METRICS) | set(HEART_METRICS.values())
# Recorded by the server itself.
SERVER_METRICS = {'join', 'leave', 'install'}

MAX_ENTRIES = 200
MAX_COUNT = 500


BUCKET_MINUTES = 5


def bucket_start(dt=None):
    """Start of the UTC 5-minute window containing dt."""
    dt = (dt or timezone.now()).astimezone(dt_timezone.utc)
    return dt.replace(minute=dt.minute - dt.minute % BUCKET_MINUTES, second=0, microsecond=0)


hour_bucket = bucket_start  # old name


def record(event, metric, *, detail='', platform='', count=1, at=None):
    """Add `count` to one bucket. Safe under concurrency (one upsert)."""
    _add([(event.pk if event is not None else None, bucket_start(at), metric, detail, platform, count)])


def _add(rows):
    """Add to many buckets in one statement: INSERT … ON CONFLICT (usage_bucket_unique) DO UPDATE count + n.

    `rows`: (event_id, bucket, metric, detail, platform, count). Atomic per row under concurrency, like the
    old update / create / update sequence, but one query for a whole batch instead of 1–4 per bucket."""
    merged = {}
    for event_id, bucket, metric, detail, platform, count in rows:
        if count <= 0:
            continue
        key = (event_id, bucket, metric, str(detail or '')[:32], platform if platform in ('ios', 'android') else '')
        merged[key] = merged.get(key, 0) + count
    if not merged:
        return
    table = connection.ops.quote_name(UsageCount._meta.db_table)
    values = ', '.join(['(%s, %s, %s, %s, %s, %s)'] * len(merged))
    params = [p for key, count in merged.items() for p in (*key, count)]
    with connection.cursor() as cursor:
        cursor.execute(
            f'INSERT INTO {table} (event_id, bucket, metric, detail, platform, count) VALUES {values} '
            f'ON CONFLICT ON CONSTRAINT usage_bucket_unique DO UPDATE SET count = {table}.count + EXCLUDED.count',
            params)


def ingest(event, platform, entries, now=None, save=True):
    """Validate and record a batch from an app. Returns how many entries were accepted.

    Unknown metrics, invalid details, ids from other events and out-of-range times are dropped
    silently: analytics must never make an app request fail. ``save=False`` validates and counts
    exactly the same but records nothing (CI keys, API-008).
    """
    now = now or timezone.now()
    if not isinstance(entries, list):
        return 0
    entries = entries[:MAX_ENTRIES]
    location_ids = item_ids = notification_ids = None
    totals = {}
    accepted = 0
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        metric = entry.get('name')
        if metric not in CLIENT_METRICS:
            continue
        kind = CLIENT_METRICS[metric]
        detail = str(entry.get('detail') or '').strip().lower()
        if kind is None:
            detail = ''
        elif kind == 'bucket':
            if detail not in PERF_METRICS[metric]:
                continue  # an unknown bucket would mean nothing
        elif kind == 'tab':
            if detail not in TABS:
                continue
        elif metric in HEART_SET and not detail:
            continue  # a heart without the id of what was hearted means nothing
        elif detail:
            if not detail.isdigit():
                continue
            if kind == 'location':
                if location_ids is None:
                    location_ids = {str(i) for i in event.location_set.values_list('id', flat=True)}
                if detail not in location_ids:
                    continue
            elif kind == 'item':
                if item_ids is None:
                    item_ids = {str(i) for i in event.scheduleditem_set.values_list('id', flat=True)}
                if detail not in item_ids:
                    continue
            elif kind == 'notification':
                if notification_ids is None:
                    notification_ids = {str(i) for i in event.notification_set.values_list('id', flat=True)}
                if detail not in notification_ids:
                    detail = ''
        try:
            count = int(entry.get('count', 1))
        except (TypeError, ValueError):
            continue
        if count < 1:
            continue
        at = parse_datetime(str(entry.get('at') or '')) if entry.get('at') else None
        if at is None or timezone.is_naive(at) or not (now - timedelta(days=7) <= at <= now + timedelta(minutes=10)):
            at = now
        key = (metric, detail, bucket_start(at))
        totals[key] = min(totals.get(key, 0) + count, MAX_COUNT)
        accepted += 1
    if not save:
        return accepted
    _add([(event.pk, bucket, metric, detail, platform, count) for (metric, detail, bucket), count in totals.items()])
    return accepted
