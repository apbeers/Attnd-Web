"""Structured request log for the API (API-013).

Every `/api/` request is logged as one JSON line on stdout (Heroku logs, logger `attnd.request`):

    {"type": "api", "method": "GET", "path": "/api/v2/events/{id}/bundle/", "status": 304, "ms": 12,
     "req_id": "4f0c…", "diag": "D-7K3QX", "platform": "ios"}

- `path` is the URL pattern, never the real URL: ids become `{id}` (any `<int:…>` or `…_id` part), other
  parts `{name}`, and the query string is never logged.
- `req_id` is the app's `X-Request-Id` (8–64 of `A-Z a-z 0-9 . _ : -`), else a new random one; it is sent
  back in the response's `X-Request-Id` header either way.
- `diag` is `X-Diag-Session` (a debug code) when the app is in a debug session. Then the line is also
  stored as a DiagnosticEntry (source "server", category "server") in that session, so it sits in the
  app's timeline next to the app's own `net.request.*` line with the same `req_id`. Only for requests that
  passed API authentication, never for CI keys, and not while the diagnostics tables are over their cap.
- Timings feed the panel's endpoint table (diagnostics.timing).
"""
import json
import logging
import re
import secrets
import time

from django.utils import timezone

from diagnostics import records, timing

logger = logging.getLogger('attnd.request')
CONVERTER = re.compile(r'<(?:(?P<conv>\w+):)?(?P<name>\w+)>')


def path_template(request):
    match = getattr(request, 'resolver_match', None)
    route = getattr(match, 'route', None) if match else None
    if not route:
        return 'unmatched'
    if '(?P' in route or route.startswith('^'):
        return '/' + re.sub(r'\(\?P<(\w+)>[^)]*\)', r'{\1}', route.lstrip('^').rstrip('$')) + '*'

    def placeholder(m):
        return '{id}' if m['conv'] == 'int' or m['name'] == 'id' or m['name'].endswith('_id') else '{' + m['name'] + '}'
    return '/' + CONVERTER.sub(placeholder, route)


def _level(status):
    from diagnostics.models import LEVELS
    return LEVELS.index('error' if status >= 500 else 'warning' if status >= 400 else 'info')


def store_server_line(code, request, line, now=None):
    """Add the request line to the debug session `code` (made if the upload hasn't arrived yet)."""
    from django.db.models import F

    from diagnostics import storage
    from diagnostics.models import LEVEL_ERROR, DiagnosticEntry, DiagnosticSession
    now = now or timezone.now()
    if storage.over_cap():
        return
    platform = getattr(request, 'attested_platform', '') or ''
    session, _ = DiagnosticSession.objects.get_or_create(
        code=code, defaults={'platform': platform if platform in ('ios', 'android') else '', 'last_entry_at': now})
    msg = f"{line['method']} {line['path']} {line['status']} in {line['ms']} ms"
    fields = {k: line[k] for k in ('method', 'path', 'status', 'ms', 'req_id')}
    level = _level(line['status'])
    DiagnosticEntry.objects.create(
        session=session, received_at=now, ts=now, level=level, cat='server', name='server.request', msg=msg,
        fields=fields, req_id=line['req_id'], source=DiagnosticEntry.SOURCE_SERVER)
    DiagnosticSession.objects.filter(pk=session.pk).update(
        entry_count=F('entry_count') + 1, bytes=F('bytes') + 220 + len(msg),
        error_count=F('error_count') + (1 if level >= LEVEL_ERROR else 0), last_entry_at=now)


class RequestLogMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not request.path.startswith('/api/'):
            return self.get_response(request)
        started = time.monotonic()
        req_id = records.clean_request_id(request.headers.get('X-Request-Id', '')) or secrets.token_hex(8)
        request.request_id = req_id
        diag = records.clean_code(request.headers.get('X-Diag-Session', ''))
        response = self.get_response(request)
        ms = round((time.monotonic() - started) * 1000)
        response['X-Request-Id'] = req_id
        line = {'type': 'api', 'method': request.method, 'path': path_template(request),
                'status': response.status_code, 'ms': ms, 'req_id': req_id}
        if diag:
            line['diag'] = diag
        platform = getattr(request, 'attested_platform', None)
        if platform:
            line['platform'] = platform
        logger.info(json.dumps(line, separators=(',', ':')))
        timing.record(request.method, line['path'], response.status_code, ms)
        if (diag and getattr(request, 'api_authenticated', False) and not getattr(request, 'ci_key', False)
                and '/diagnostics/' not in request.path):  # not the uploads themselves
            try:
                store_server_line(diag, request, line)
            except Exception:
                logger.exception('Could not store the request line for a debug session')
        return response
