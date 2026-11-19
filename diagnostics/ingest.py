"""Uploads from the apps (API-013): debug log batches and anonymous crash/hang reports.

Limits (API_V2.md → Diagnostics):
- a request body is at most 256 KB as sent (gzip or plain JSON) and 2 MB once decompressed (413 otherwise);
- a log batch has at most 500 entries (413);
- a session accepts at most 20,000 entries per 10 minutes (429 + Retry-After until the window ends);
- while the diagnostics tables are above their size cap (storage.py), uploads answer 429 + Retry-After 3600.
Invalid records are dropped one by one (counted in `rejected`); a bad batch never fails because of one record.
"""
import hashlib
import json
import zlib
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from diagnostics import records, storage
from diagnostics.models import LEVEL_ERROR, REASON_DEV, REASONS, DiagnosticEntry, DiagnosticReport, DiagnosticSession

MAX_BODY_BYTES = 256 * 1024
MAX_DECOMPRESSED_BYTES = 2 * 1024 * 1024
MAX_ENTRIES = 500
MAX_REPORTS = 20
MAX_REPORT_BYTES = 256 * 1024
MAX_STACK_CHARS = 128 * 1024
PLATFORMS = ('ios', 'android')


class Refused(Exception):
    """An upload the server won't take: answered with `status` (and Retry-After when set)."""

    def __init__(self, status, error, retry_after=None):
        super().__init__(error)
        self.status = status
        self.error = error
        self.retry_after = retry_after


def read_json(request, max_body=MAX_BODY_BYTES, max_decompressed=MAX_DECOMPRESSED_BYTES):
    """The request's JSON object, gzip-decoded when it is gzip (Content-Encoding: gzip or the gzip magic)."""
    try:
        length = int(request.META.get('CONTENT_LENGTH') or 0)
    except ValueError:
        length = 0
    if length > max_body:
        raise Refused(413, f'Body larger than {max_body // 1024} KB')
    raw = request.read(max_body + 1)
    if len(raw) > max_body:
        raise Refused(413, f'Body larger than {max_body // 1024} KB')
    if request.headers.get('Content-Encoding', '').lower() == 'gzip' or raw[:2] == b'\x1f\x8b':
        raw = gunzip(raw, max_decompressed)
    try:
        data = json.loads(raw or b'{}')
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise Refused(400, 'Body is not JSON')
    if not isinstance(data, dict):
        raise Refused(400, 'Body must be a JSON object')
    return data


def gunzip(raw, limit):
    """Decompress gzip without ever holding more than `limit` bytes (zip bombs)."""
    inflater = zlib.decompressobj(wbits=31)
    try:
        out = inflater.decompress(raw, limit + 1)
    except zlib.error:
        raise Refused(400, 'Body is not valid gzip')
    if len(out) > limit or inflater.unconsumed_tail:
        raise Refused(413, f'Body larger than {limit // 1024} KB once decompressed')
    return out


def _text(value, limit):
    return value.strip()[:limit] if isinstance(value, str) else ''


def _check_room():
    if storage.over_cap():
        raise Refused(429, 'Diagnostics storage is full', retry_after=3600)


def ingest_logs(body, *, save=True, now=None):
    """Validate and store one log batch. Returns (accepted, rejected). Raises Refused."""
    now = now or timezone.now()
    code = records.clean_code(body.get('session'))
    if not code:
        raise Refused(400, 'session must be a debug code like "D-7K3QX"')
    platform = body.get('platform')
    if platform not in PLATFORMS:
        raise Refused(400, 'platform must be "ios" or "android"')
    reason = body.get('reason')
    if reason not in REASONS:
        raise Refused(400, 'reason must be "dev" or "debug_mode"')
    entries = body.get('entries')
    if not isinstance(entries, list):
        raise Refused(400, 'entries must be a list')
    if len(entries) > MAX_ENTRIES:
        raise Refused(413, f'At most {MAX_ENTRIES} entries per request')

    oldest = settings.DIAGNOSTICS_RETENTION_DAYS.get(reason, 14)
    cleaned = []
    for entry in entries:
        row = records.clean_entry(entry, code, now=now, oldest_days=oldest, allow_text=reason == REASON_DEV)
        if row is not None:
            cleaned.append(row)
    rejected = len(entries) - len(cleaned)
    if not save:
        return len(cleaned), rejected  # CI keys: the same answer, nothing stored (API-008)
    _check_room()
    if not cleaned:
        return 0, rejected

    window = timedelta(seconds=settings.DIAGNOSTICS_SESSION_WINDOW)
    limit = settings.DIAGNOSTICS_SESSION_MAX_ENTRIES
    with transaction.atomic():
        session, _ = DiagnosticSession.objects.select_for_update().get_or_create(
            code=code, defaults={'platform': platform, 'reason': reason, 'last_entry_at': now})
        if session.window_start is None or now - session.window_start >= window:
            session.window_start, session.window_count = now, 0
        if session.window_count + len(cleaned) > limit:
            retry = max(1, int((session.window_start + window - now).total_seconds()) + 1)
            raise Refused(429, f'At most {limit} entries per session every {window.seconds // 60} minutes',
                          retry_after=retry)
        session.window_count += len(cleaned)
        session.platform = platform
        session.reason = reason
        for key, limit_ in (('app', 32), ('build', 32), ('os', 48), ('model', 48)):
            value = _text(body.get(key), limit_)
            if value:
                setattr(session, 'app_version' if key == 'app' else key, value)
        objs = []
        for row in cleaned:
            size = row.pop('_bytes')
            session.bytes += size
            objs.append(DiagnosticEntry(session=session, received_at=now, **row))
        DiagnosticEntry.objects.bulk_create(objs, batch_size=500)
        stamps = [o.ts for o in objs]
        session.first_ts = min([session.first_ts, *stamps] if session.first_ts else stamps)
        session.last_ts = max([session.last_ts, *stamps] if session.last_ts else stamps)
        session.entry_count += len(objs)
        session.error_count += sum(1 for o in objs if o.level >= LEVEL_ERROR)
        latest_event = next((o.event_id for o in sorted(objs, key=lambda o: o.ts, reverse=True) if o.event_id), None)
        if latest_event:
            session.event_id = latest_event
        session.last_entry_at = now
        session.save()
    return len(objs), rejected


# ---------------------------------------------------------------------------
# Crash / hang / exit reports (anonymous)
# ---------------------------------------------------------------------------

def _first_line(value, limit=500):
    return (value.strip().splitlines() or [''])[0][:limit] if isinstance(value, str) else ''


def signature_for(platform, kind, summary, stack, payload):
    """A stable hash that groups reports of the same problem: kind, exception type and the top frames."""
    exc_type = summary.split(':', 1)[0].strip()
    frames = []
    if stack:
        frames = [ln.strip() for ln in stack.splitlines() if ln.strip().startswith('at ')][:4]
    elif isinstance(payload, dict):
        from diagnostics.symbolicate import frames_of
        frames = [f"{f['binary']}+{f['offset']}" for f in frames_of(payload)[:4]]
    text = '\n'.join([platform, kind, exc_type, *frames])
    return hashlib.sha1(text.encode()).hexdigest()


def clean_report(report, platform, defaults, now):
    if not isinstance(report, dict):
        return None
    kind = report.get('kind')
    if kind not in DiagnosticReport.KINDS:
        return None
    occurred = parse_datetime(report['occurred_at']) if isinstance(report.get('occurred_at'), str) else None
    if occurred is not None and (timezone.is_naive(occurred) or occurred > now + timedelta(days=1)):
        occurred = None
    exception = report.get('exception') if isinstance(report.get('exception'), dict) else {}
    exc_type = _text(exception.get('type'), 200)
    exc_message = _first_line(exception.get('message'), 280)
    summary = _text(report.get('summary'), 500) or (f'{exc_type}: {exc_message}' if exc_message else exc_type)
    stack = report.get('stack') if isinstance(report.get('stack'), str) else ''
    stack = stack[:MAX_STACK_CHARS]
    payload = report.get('payload') if isinstance(report.get('payload'), dict) else None
    size = len(stack) + (len(json.dumps(payload)) if payload is not None else 0)
    if size > MAX_REPORT_BYTES:
        return None
    return DiagnosticReport(
        occurred_at=occurred, platform=platform, kind=kind,
        app_version=_text(report.get('app'), 32) or defaults['app_version'],
        build=_text(report.get('build'), 32) or defaults['build'],
        os=_text(report.get('os'), 48) or defaults['os'],
        model=_text(report.get('model'), 48) or defaults['model'],
        reason=_text(report.get('reason'), 200), summary=summary[:500],
        signature=signature_for(platform, kind, summary, stack, payload),
        stack=stack, payload=payload, diag=records.clean_code(report.get('diag')), bytes=size + 300)


def ingest_reports(body, *, save=True, now=None):
    """Validate and store a batch of reports. Returns (accepted, rejected). Raises Refused."""
    now = now or timezone.now()
    platform = body.get('platform')
    if platform not in PLATFORMS:
        raise Refused(400, 'platform must be "ios" or "android"')
    reports = body.get('reports')
    if not isinstance(reports, list):
        raise Refused(400, 'reports must be a list')
    if len(reports) > MAX_REPORTS:
        raise Refused(413, f'At most {MAX_REPORTS} reports per request')
    defaults = {'app_version': _text(body.get('app'), 32), 'build': _text(body.get('build'), 32),
                'os': _text(body.get('os'), 48), 'model': _text(body.get('model'), 48)}
    cleaned = [r for r in (clean_report(r, platform, defaults, now) for r in reports) if r is not None]
    rejected = len(reports) - len(cleaned)
    if not save:
        return len(cleaned), rejected
    _check_room()
    DiagnosticReport.objects.bulk_create(cleaned)
    return len(cleaned), rejected

