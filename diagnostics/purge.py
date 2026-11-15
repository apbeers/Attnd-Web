"""Retention and the size cap for diagnostics (API-013). Run by `run_scheduled_tasks` (and `purge_diagnostics`).

1. Log entries older than their session's retention (dev builds 7 days, production debug mode 14 days;
   settings.DIAGNOSTICS_RETENTION_DAYS) are deleted, then sessions with nothing left.
2. Reports older than 30 days, endpoint timings older than 30 days and all but the newest
   settings.DIAGNOSTICS_MAX_SYMBOL_FILES mapping files are deleted.
3. If the diagnostics tables are still at or above settings.DIAGNOSTICS_MAX_BYTES (250 MB), the oldest
   sessions (by their last upload) are deleted until the estimate is under 80 % of the cap, then the oldest
   reports if needed, and VACUUM FULL gives the space back so the size check sees it.
Every step is idempotent and cheap when there is nothing to do.
"""
import logging
from datetime import timedelta

from django.conf import settings
from django.db import connection
from django.db.models import Sum
from django.utils import timezone

from diagnostics import storage
from diagnostics.models import DiagnosticEntry, DiagnosticReport, DiagnosticSession, EndpointTiming, SymbolFile

logger = logging.getLogger(__name__)
REPORT_DAYS = 30
TIMING_DAYS = 30
TARGET_FRACTION = 0.8


def trim_symbol_files():
    keep = settings.DIAGNOSTICS_MAX_SYMBOL_FILES
    old = list(SymbolFile.objects.order_by('-uploaded_at').values_list('pk', flat=True)[keep:])
    if old:
        SymbolFile.objects.filter(pk__in=old).delete()
    return len(old)


def _vacuum():
    """Give deleted space back to the OS (only outside a transaction; tests run inside one)."""
    if connection.in_atomic_block or not settings.DIAGNOSTICS_VACUUM_FULL:
        return False
    with connection.cursor() as cursor:
        for table in storage.TABLES:
            cursor.execute(f'VACUUM FULL {table}')
    return True


def purge(now=None):
    now = now or timezone.now()
    result = {'entries': 0, 'sessions': 0, 'reports': 0, 'timings': 0, 'symbols': 0, 'capped_sessions': 0,
              'vacuumed': False}
    retention = settings.DIAGNOSTICS_RETENTION_DAYS
    default_days = max(retention.values())
    for reason, days in retention.items():
        cutoff = now - timedelta(days=days)
        result['entries'] += DiagnosticEntry.objects.filter(session__reason=reason, received_at__lt=cutoff).delete()[0]
    result['entries'] += DiagnosticEntry.objects.exclude(session__reason__in=list(retention)).filter(
        received_at__lt=now - timedelta(days=default_days)).delete()[0]
    shortest = now - timedelta(days=min(retention.values()))
    for session in DiagnosticSession.objects.filter(last_entry_at__lt=shortest).only('pk', 'reason', 'last_entry_at'):
        days = retention.get(session.reason, default_days)
        if session.last_entry_at < now - timedelta(days=days) and not session.entries.exists():
            session.delete()
            result['sessions'] += 1
    result['reports'] = DiagnosticReport.objects.filter(received_at__lt=now - timedelta(days=REPORT_DAYS)).delete()[0]
    result['timings'] = EndpointTiming.objects.filter(hour__lt=now - timedelta(days=TIMING_DAYS)).delete()[0]
    result['symbols'] = trim_symbol_files()

    used = storage.used_bytes(fresh=True)
    cap = storage.cap_bytes()
    if used >= cap:
        result.update(_enforce_cap(used, cap))
        result['vacuumed'] = _vacuum()
        storage.forget()
    return result


def _enforce_cap(used, cap):
    """Delete the oldest sessions (then reports) until the estimated size is below TARGET_FRACTION of the cap.

    The physical size can't be re-measured row by row (deleted rows keep their space until VACUUM), so each
    session's share is estimated from its recorded bytes, scaled to what the tables really take."""
    logical = (DiagnosticSession.objects.aggregate(n=Sum('bytes'))['n'] or 0) + \
        (DiagnosticReport.objects.aggregate(n=Sum('bytes'))['n'] or 0)
    scale = used / logical if logical else 1.0
    to_free = used - cap * TARGET_FRACTION
    freed = 0.0
    sessions = 0
    for pk, size in DiagnosticSession.objects.order_by('last_entry_at', 'pk').values_list('pk', 'bytes').iterator():
        if freed >= to_free:
            break
        DiagnosticSession.objects.filter(pk=pk).delete()
        freed += (size or 0) * scale
        sessions += 1
    reports = 0
    if freed < to_free:
        for pk, size in DiagnosticReport.objects.order_by('received_at', 'pk').values_list('pk', 'bytes').iterator():
            if freed >= to_free:
                break
            DiagnosticReport.objects.filter(pk=pk).delete()
            freed += (size or 0) * scale
            reports += 1
    logger.warning('Diagnostics over their cap (%d of %d bytes): deleted %d sessions and %d reports',
                   used, cap, sessions, reports)
    return {'capped_sessions': sessions, 'capped_reports': reports}
