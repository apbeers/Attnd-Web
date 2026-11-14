"""Anonymous usage counting (API-005).

Counts are bucketed per event, UTC 5-minute window, metric, detail and platform. Nothing identifying is
stored: callers pass no device ID, and the ingest endpoint never reads one.
"""
from datetime import timedelta, timezone as dt_timezone

from django.db import IntegrityError, transaction
from django.db.models import F
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from events.models import UsageCount

TABS = {'map', 'locations', 'schedule', 'flyer', 'notifications', 'options'}

# Metrics the apps may send, and what their `detail` may be.
CLIENT_METRICS = {
    'app_open': None,               # the app came to the foreground while in this event
    'tab_view': 'tab',              # detail: one of TABS
    'location_view': 'location',    # detail: location id
    'schedule_item_view': 'item',   # detail: schedule item id
    'notification_open': 'notification',  # a push was tapped; detail: notification id (optional)
    'search': None,                 # a search was run (never the search text)
    'refresh': None,                # pull to refresh
}
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
    """Add `count` to one bucket. Safe under concurrency (update, else create, else update)."""
    if count <= 0:
        return
    key = {'event': event, 'bucket': bucket_start(at), 'metric': metric, 'detail': str(detail or '')[:32],
           'platform': platform if platform in ('ios', 'android') else ''}
    if UsageCount.objects.filter(**key).update(count=F('count') + count):
        return
    try:
        with transaction.atomic():
            UsageCount.objects.create(count=count, **key)
    except IntegrityError:
        UsageCount.objects.filter(**key).update(count=F('count') + count)


def ingest(event, platform, entries, now=None):
    """Validate and record a batch from an app. Returns how many entries were accepted.

    Unknown metrics, invalid details, ids from other events and out-of-range times are dropped
    silently: analytics must never make an app request fail.
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
        elif kind == 'tab':
            if detail not in TABS:
                continue
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
    for (metric, detail, bucket), count in totals.items():
        record(event, metric, detail=detail, platform=platform, count=count, at=bucket)
    return accepted
