"""Diagnostics panel (API-013) in the console's staff area: /console/diagnostics/.

Staff only (is_staff or superuser; the console's two-factor middleware already requires a strong second
factor for them); everyone else gets a 404. All log text comes from apps, so templates escape it and the
JavaScript only ever sets textContent.
"""
import json
from datetime import datetime, time, timedelta
from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Max, Q, Sum
from django.http import Http404, JsonResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views.decorators.http import require_POST

from diagnostics import records, retrace, storage, summary, symbolicate, timing
from diagnostics.models import (APP_CATEGORIES, LEVELS, REASONS, SERVER_CATEGORY, DiagnosticReport,
                                DiagnosticSession, SymbolFile)
from events.models import Event, UsageCount
from events.usage import PERF_METRICS


def staff_required(view):
    @login_required
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not (request.user.is_staff or request.user.is_superuser):
            raise Http404
        return view(request, *args, **kwargs)
    return wrapper


def _day(value, end=False):
    day = parse_date(value or '')
    if day is None:
        return None
    moment = datetime.combine(day, time.max if end else time.min)
    return timezone.make_aware(moment, timezone.get_current_timezone())


def _event_names(ids):
    return dict(Event.objects.filter(pk__in=[i for i in ids if i]).values_list('pk', 'name'))


def _perf(days=7):
    since = timezone.now() - timedelta(days=days)
    rows = (UsageCount.objects.filter(metric__in=list(PERF_METRICS), bucket__gte=since)
            .values('metric', 'detail', 'platform').annotate(n=Sum('count')))
    table = {}
    for row in rows:
        metric = table.setdefault(row['metric'], {'metric': row['metric'], 'buckets': {}, 'total': 0})
        bucket = metric['buckets'].setdefault(row['detail'], {'ios': 0, 'android': 0, 'all': 0})
        bucket[row['platform'] or 'all'] = bucket.get(row['platform'] or 'all', 0) + row['n']
        if row['platform']:
            bucket['all'] += row['n']
        metric['total'] += row['n']
    out = []
    for name, buckets in PERF_METRICS.items():
        if name not in table:
            continue
        metric = table[name]
        metric['rows'] = [{'bucket': b or '(none)', **metric['buckets'][b]} for b in buckets if b in metric['buckets']]
        out.append(metric)
    return out


@staff_required
def overview(request):
    sessions = DiagnosticSession.objects.all()
    q = request.GET.get('q', '').strip()
    if q:
        code = q.upper() if q.upper().startswith('D-') else 'D-' + q.upper()
        sessions = sessions.filter(code__startswith=code[:7])
    for key in ('platform', 'reason'):
        if request.GET.get(key):
            sessions = sessions.filter(**{key: request.GET[key]})
    if request.GET.get('app'):
        sessions = sessions.filter(Q(app_version__icontains=request.GET['app']) | Q(build=request.GET['app']))
    event_q = request.GET.get('event', '').strip()
    if event_q:
        if event_q.isdigit():
            sessions = sessions.filter(event_id=int(event_q))
        else:
            sessions = sessions.filter(event_id__in=Event.objects.filter(event_code__iexact=event_q).values('pk'))
    if request.GET.get('errors'):
        sessions = sessions.filter(error_count__gt=0)
    since, until = _day(request.GET.get('since')), _day(request.GET.get('until'), end=True)
    if since:
        sessions = sessions.filter(last_entry_at__gte=since)
    if until:
        sessions = sessions.filter(started_at__lte=until)
    sessions = list(sessions[:200])
    names = _event_names({s.event_id for s in sessions})
    for s in sessions:
        s.event_name = names.get(s.event_id, '')

    week = timezone.now() - timedelta(days=7)
    groups = (DiagnosticReport.objects.filter(received_at__gte=week).values('signature', 'platform', 'kind')
              .annotate(n=Count('id'), latest=Max('received_at'), last_id=Max('id')).order_by('-n')[:15])
    latest = {r.pk: r for r in DiagnosticReport.objects.filter(pk__in=[g['last_id'] for g in groups])}
    report_groups = [{**g, 'report': latest.get(g['last_id'])} for g in groups]
    used, cap = storage.used_bytes(), storage.cap_bytes()
    return render(request, 'diagnostics/overview.html', {
        'section': 'diagnostics', 'sessions': sessions, 'filters': request.GET, 'reasons': REASONS,
        'used_mb': used / 1024 / 1024, 'cap_mb': cap / 1024 / 1024, 'used_pct': min(100, round(100 * used / cap)) if cap else 0,
        'report_groups': report_groups, 'perf': _perf(),
        'symbol_count': SymbolFile.objects.count(),
    })


@staff_required
def session_detail(request, code):
    session = get_object_or_404(DiagnosticSession, code=code.upper())
    event = Event.objects.filter(pk=session.event_id).first() if session.event_id else None
    return render(request, 'diagnostics/session.html', {
        'section': 'diagnostics', 'session': session, 'event_obj': event, 'summary': summary.build(session),
        'reports': DiagnosticReport.objects.filter(diag=session.code)[:20],
        'levels': LEVELS, 'categories': (*APP_CATEGORIES, SERVER_CATEGORY), 'limit': summary.MAX_SCAN,
    })


@staff_required
def session_entries(request, code):
    session = get_object_or_404(DiagnosticSession, code=code.upper())
    rows = summary.timeline(session)
    return JsonResponse({'code': session.code, 'levels': LEVELS, 'categories': [*APP_CATEGORIES, SERVER_CATEGORY],
                         'total': session.entry_count, 'entries': rows})


@staff_required
def session_export(request, code):
    session = get_object_or_404(DiagnosticSession, code=code.upper())

    def lines():
        for entry in session.entries.order_by('ts', 'id').iterator(chunk_size=2000):
            yield json.dumps(records.record_dict(entry, session), separators=(',', ':')) + '\n'
    response = StreamingHttpResponse(lines(), content_type='application/x-ndjson')
    response['Content-Disposition'] = f'attachment; filename="attnd-{session.code}.jsonl"'
    return response


@staff_required
@require_POST
def session_delete(request, code):
    session = get_object_or_404(DiagnosticSession, code=code.upper())
    session.delete()
    storage.forget()
    messages.success(request, f'Deleted session {code.upper()}.')
    return redirect('diagnostics:overview')


@staff_required
def reports(request):
    rows = DiagnosticReport.objects.all()
    for key in ('platform', 'kind'):
        if request.GET.get(key):
            rows = rows.filter(**{key: request.GET[key]})
    if request.GET.get('app'):
        rows = rows.filter(Q(app_version__icontains=request.GET['app']) | Q(build=request.GET['app']))
    if request.GET.get('signature'):
        rows = rows.filter(signature=request.GET['signature'])
    return render(request, 'diagnostics/reports.html', {
        'section': 'diagnostics', 'reports': rows.defer('payload', 'stack', 'retraced')[:200],
        'filters': request.GET, 'kinds': DiagnosticReport.KINDS})


def mapping_for(report):
    return (SymbolFile.objects.filter(platform='android', kind=SymbolFile.KIND_R8, build=report.build).first()
            if report.build else None)


@staff_required
def report_detail(request, pk):
    report = get_object_or_404(DiagnosticReport, pk=pk)
    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'symbolicate':
            ok, message = symbolicate.request(report, site_url=request.build_absolute_uri('/').rstrip('/'))
            (messages.success if ok else messages.error)(request, message)
        elif action == 'retrace':
            mapping = mapping_for(report)
            if mapping is None or not report.stack:
                messages.error(request, f'No R8 mapping for build {report.build or "?"} (CI uploads one per release).')
            else:
                report.retraced = retrace.retrace(report.stack, mapping.text())
                report.save(update_fields=['retraced'])
                messages.success(request, f'Retraced with the mapping of {mapping.app_version} ({mapping.build}).')
        elif action == 'delete':
            report.delete()
            messages.success(request, 'Report deleted.')
            return redirect('diagnostics:reports')
        return redirect('diagnostics:report', pk=report.pk)
    mapping = mapping_for(report) if report.platform == 'android' else None
    if mapping is not None and report.stack and not report.retraced:
        report.retraced = retrace.retrace(report.stack, mapping.text())
        report.save(update_fields=['retraced'])
    return render(request, 'diagnostics/report.html', {
        'section': 'diagnostics', 'report': report, 'mapping': mapping,
        'ios_text': symbolicate.render(report) if report.platform == 'ios' and report.payload else '',
        'can_symbolicate': report.platform == 'ios' and bool(symbolicate.frames_of(report.payload or {})),
        'symbolicate_configured': symbolicate.configured(),
        'payload_json': json.dumps(report.payload, indent=2)[:200_000] if report.payload else '',
        'same': DiagnosticReport.objects.filter(signature=report.signature).count(),
    })


@staff_required
def endpoints(request):
    window = request.GET.get('window', '24h')
    hours = {'1h': 1, '24h': 24, '7d': 24 * 7, '30d': 24 * 30}.get(window, 24)
    since = (timezone.now() - timedelta(hours=hours)).replace(minute=0, second=0, microsecond=0)
    timing.flush()
    return render(request, 'diagnostics/endpoints.html', {
        'section': 'diagnostics', 'rows': timing.table(since), 'window': window,
        'windows': ['1h', '24h', '7d', '30d']})


@staff_required
def symbols(request):
    result = None
    if request.method == 'POST':
        if request.POST.get('action') == 'delete':
            SymbolFile.objects.filter(pk=request.POST.get('id') or 0).delete()
            messages.success(request, 'Mapping deleted.')
            return redirect('diagnostics:symbols')
        mapping = SymbolFile.objects.filter(pk=request.POST.get('mapping') or 0).first()
        stack = request.POST.get('stack', '')[:200_000]
        if mapping is None or not stack.strip():
            messages.error(request, 'Choose a mapping and paste a stack trace.')
        else:
            result = retrace.retrace(stack, mapping.text())
    return render(request, 'diagnostics/symbols.html', {
        'section': 'diagnostics', 'files': SymbolFile.objects.defer('data'), 'result': result,
        'stack': request.POST.get('stack', '') if request.method == 'POST' else '',
        'selected': request.POST.get('mapping', '')})
