import json
import logging

from django.db import connection
from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from attnd.ratelimit import RateLimit, client_ip

csp_logger = logging.getLogger('attnd.csp')
_CSP_REPORTS = RateLimit('csp_report_ip')


def health(request):
    """Unauthenticated liveness check used by the deploy pipeline."""
    try:
        with connection.cursor() as cursor:
            cursor.execute('SELECT 1')
        db = 'ok'
    except Exception:
        db = 'error'
    status = 200 if db == 'ok' else 503
    return JsonResponse({'status': 'ok' if status == 200 else 'degraded', 'database': db}, status=status)


_CSP_REPORT_MAX_BYTES = 8 * 1024


@csrf_exempt
@require_POST
def csp_report(request):
    """Browsers post Content-Security-Policy violations here (report-uri); they are logged (attnd.csp)
    so a report-only rollout (CSP_MODE=report) shows what would break. Size- and rate-limited."""
    if not _CSP_REPORTS.exceeded(client_ip(request)):
        _CSP_REPORTS.hit(client_ip(request))
        body = request.body[:_CSP_REPORT_MAX_BYTES]
        try:
            report = json.loads(body).get('csp-report', {})
        except (ValueError, AttributeError):
            report = {}
        if isinstance(report, dict) and report:
            csp_logger.warning('CSP violation: %s blocked %s on %s', report.get('violated-directive')
                               or report.get('effective-directive'), report.get('blocked-uri'),
                               report.get('document-uri'))
    return HttpResponse(status=204)
