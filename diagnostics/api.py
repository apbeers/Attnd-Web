"""Diagnostics endpoints (API-013, API_V2.md → Diagnostics).

    POST /api/v2/diagnostics/logs/      app auth; a debug session's log batch (gzip JSON)
    POST /api/v2/diagnostics/reports/   app auth; anonymous crash / hang / exit reports
    POST /api/diagnostics/symbols/      CI only; an Android R8 mapping.txt for one build
    GET|POST /api/diagnostics/symbolicate/<report id>/   attnd-ios's symbolicate.yml (one-time token)
"""
import gzip
import hashlib

from django.http import JsonResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from attnd.ratelimit import RateLimit, client_ip
from diagnostics import ingest, symbolicate
from diagnostics.ingest import Refused
from diagnostics.models import SymbolFile
from events.apis import ApiKeyRequired, ci_key_ok, is_ci_request, static_key_ok

MAX_SYMBOLS_BODY = 25 * 1024 * 1024  # gzip on the wire
MAX_SYMBOLS_TEXT = 200 * 1024 * 1024


def refused(error):
    response = JsonResponse({'error': error.error, **({'retry_after': error.retry_after} if error.retry_after else {})},
                            status=error.status)
    if error.retry_after:
        response['Retry-After'] = str(error.retry_after)
    return response


def _ip_limited(request, name):
    limit = RateLimit(name)
    ip = client_ip(request)
    if limit.hit(ip) > limit.limit:
        return refused(Refused(429, 'Too many uploads from this network', retry_after=limit.window))
    return None


@method_decorator(csrf_exempt, name='dispatch')
class DiagnosticLogsV2(ApiKeyRequired, View):
    ci_scope = 'diagnostics'

    def post(self, request):
        if limited := _ip_limited(request, 'diagnostics_ip'):
            return limited
        try:
            body = ingest.read_json(request)
            accepted, rejected = ingest.ingest_logs(body, save=not is_ci_request(request))
        except Refused as error:
            return refused(error)
        return JsonResponse({'status': 'ok', 'accepted': accepted, 'rejected': rejected})


@method_decorator(csrf_exempt, name='dispatch')
class DiagnosticReportsV2(ApiKeyRequired, View):
    ci_scope = 'diagnostics'

    def post(self, request):
        if limited := _ip_limited(request, 'diagnostics_reports_ip'):
            return limited
        try:
            body = ingest.read_json(request)
            accepted, rejected = ingest.ingest_reports(body, save=not is_ci_request(request))
        except Refused as error:
            return refused(error)
        return JsonResponse({'status': 'ok', 'accepted': accepted, 'rejected': rejected})


def _bearer(request):
    return request.headers.get('Authorization', '').removeprefix('Bearer ').strip()


@method_decorator(csrf_exempt, name='dispatch')
class SymbolsUpload(View):
    """POST /api/diagnostics/symbols/?platform=android&version=2.0.131&build=131000[&kind=r8_mapping]

    Body: the mapping.txt, plain or gzip. Auth: a CI key (CI_API_KEYS) or, where it is on, the static key.
    The same build uploaded again replaces the file."""

    def post(self, request):
        bearer = _bearer(request)
        if not (ci_key_ok(bearer) or static_key_ok(bearer)):
            return JsonResponse({'error': 'Unauthorized'}, status=401)
        platform = request.GET.get('platform', 'android')
        kind = request.GET.get('kind', SymbolFile.KIND_R8)
        version = request.GET.get('version', '').strip()[:32]
        build = request.GET.get('build', '').strip()[:32]
        if platform != 'android' or kind != SymbolFile.KIND_R8:
            return JsonResponse({'error': 'Only platform=android, kind=r8_mapping is stored'}, status=400)
        if not version or not build:
            return JsonResponse({'error': 'version and build are required'}, status=400)
        try:
            raw = request.read(MAX_SYMBOLS_BODY + 1)
            if len(raw) > MAX_SYMBOLS_BODY:
                raise Refused(413, 'Mapping larger than 25 MB compressed')
            text = ingest.gunzip(raw, MAX_SYMBOLS_TEXT) if raw[:2] == b'\x1f\x8b' else raw
            if len(text) > MAX_SYMBOLS_TEXT:
                raise Refused(413, 'Mapping larger than 200 MB')
        except Refused as error:
            return refused(error)
        if not _looks_like_mapping(text):
            return JsonResponse({'error': 'Body is not an R8 mapping file'}, status=400)
        digest = hashlib.sha256(text).hexdigest()
        row, _ = SymbolFile.objects.update_or_create(
            platform=platform, kind=kind, build=build,
            defaults={'app_version': version, 'data': gzip.compress(text, compresslevel=9), 'size': len(text),
                      'sha256': digest})
        from diagnostics.purge import trim_symbol_files
        trim_symbol_files()
        return JsonResponse({'status': 'ok', 'id': row.pk, 'bytes': len(text), 'sha256': digest})


def _looks_like_mapping(text):
    for line in text[:200_000].decode('utf-8', 'replace').splitlines():
        if not line.strip() or line.startswith('#'):
            continue
        return ' -> ' in line and line.rstrip().endswith(':')
    return False


@method_decorator(csrf_exempt, name='dispatch')
class SymbolicateCallback(View):
    """The symbolicate.yml workflow: GET the frames, POST the symbols. Bearer = the one-time token."""

    def dispatch(self, request, report_id):
        report = symbolicate.report_for_token(report_id, _bearer(request))
        if report is None:
            return JsonResponse({'error': 'Unknown or expired token'}, status=401)
        self.report = report
        return super().dispatch(request, report_id)

    def get(self, request, report_id):
        return JsonResponse(symbolicate.job(self.report))

    def post(self, request, report_id):
        try:
            body = ingest.read_json(request, max_body=2 * 1024 * 1024, max_decompressed=8 * 1024 * 1024)
        except Refused as error:
            return refused(error)
        stored = symbolicate.store_result(self.report, body)
        return JsonResponse({'status': 'ok', 'symbols': stored})

