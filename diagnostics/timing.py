"""Server endpoint timings (API-013): p50/p95 per method and path template, per hour.

The request middleware calls `record()` for every API request. Counts are kept in memory (one web process,
see settings.CACHES) and written at most once a minute (`flush()`, a few upserts), so timing costs the
database almost nothing. A restart loses at most a minute of timings.
"""
import bisect
import logging
import threading
import time

from django.conf import settings
from django.db import transaction
from django.utils import timezone

logger = logging.getLogger(__name__)

# Upper bounds (ms) of the histogram buckets; the last bucket is everything slower.
BUCKETS_MS = (5, 10, 25, 50, 75, 100, 150, 250, 400, 600, 1000, 1500, 2500, 4000, 6000, 10000)
FLUSH_SECONDS = 60

_lock = threading.Lock()
_pending = {}
_last_flush = time.monotonic()


def _empty():
    return {'count': 0, 'client_errors': 0, 'server_errors': 0, 'total_ms': 0, 'max_ms': 0,
            'hist': [0] * (len(BUCKETS_MS) + 1)}


def record(method, route, status, ms, now=None):
    global _last_flush
    if not getattr(settings, 'DIAGNOSTICS_TIMING', True):
        return
    hour = (now or timezone.now()).replace(minute=0, second=0, microsecond=0)
    ms = max(0, int(ms))
    with _lock:
        agg = _pending.setdefault((hour, method[:8], route[:160]), _empty())
        agg['count'] += 1
        agg['client_errors'] += 400 <= status < 500
        agg['server_errors'] += status >= 500
        agg['total_ms'] += ms
        agg['max_ms'] = max(agg['max_ms'], ms)
        agg['hist'][bisect.bisect_left(BUCKETS_MS, ms)] += 1
        due = time.monotonic() - _last_flush >= FLUSH_SECONDS
        if due:
            _last_flush = time.monotonic()
    if due:
        flush()


def flush():
    """Write the pending counts. Never raises (timings must not break a request)."""
    from diagnostics.models import EndpointTiming
    with _lock:
        pending = dict(_pending)
        _pending.clear()
    try:
        for (hour, method, route), agg in pending.items():
            with transaction.atomic():
                row, _ = EndpointTiming.objects.select_for_update().get_or_create(
                    hour=hour, method=method, route=route, defaults={'hist': [0] * (len(BUCKETS_MS) + 1)})
                hist = list(row.hist) + [0] * (len(BUCKETS_MS) + 1 - len(row.hist))
                row.hist = [a + b for a, b in zip(hist, agg['hist'])]
                row.count += agg['count']
                row.client_errors += agg['client_errors']
                row.server_errors += agg['server_errors']
                row.total_ms += agg['total_ms']
                row.max_ms = max(row.max_ms, agg['max_ms'])
                row.save()
    except Exception:
        logger.exception('Could not write endpoint timings')


def reset():
    with _lock:
        _pending.clear()


def percentile(hist, fraction):
    """The `fraction` quantile (e.g. 0.95) of a histogram, interpolated inside its bucket, in ms."""
    total = sum(hist)
    if not total:
        return None
    target = fraction * total
    seen = 0
    lower = 0
    for index, count in enumerate(hist):
        upper = BUCKETS_MS[index] if index < len(BUCKETS_MS) else BUCKETS_MS[-1] * 2
        if count and seen + count >= target:
            return round(lower + (upper - lower) * (target - seen) / count)
        seen += count
        lower = upper
    return BUCKETS_MS[-1] * 2


def table(since):
    """Rows for the panel's endpoint table: method, route, count, errors, avg, p50, p95, max (slowest p95 first)."""
    from diagnostics.models import EndpointTiming
    merged = {}
    for row in EndpointTiming.objects.filter(hour__gte=since):
        agg = merged.setdefault((row.method, row.route), _empty())
        agg['count'] += row.count
        agg['client_errors'] += row.client_errors
        agg['server_errors'] += row.server_errors
        agg['total_ms'] += row.total_ms
        agg['max_ms'] = max(agg['max_ms'], row.max_ms)
        agg['hist'] = [a + b for a, b in zip(agg['hist'], list(row.hist) + [0] * len(agg['hist']))]
    rows = []
    for (method, route), agg in merged.items():
        rows.append({'method': method, 'route': route, 'count': agg['count'],
                     'client_errors': agg['client_errors'], 'server_errors': agg['server_errors'],
                     'avg': round(agg['total_ms'] / agg['count']) if agg['count'] else None,
                     'p50': percentile(agg['hist'], .5), 'p95': percentile(agg['hist'], .95), 'max': agg['max_ms']})
    rows.sort(key=lambda r: (-(r['p95'] or 0), r['route']))
    return rows
