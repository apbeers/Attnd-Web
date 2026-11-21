"""Ask Attnd's endpoints for the console panel (JSON; the panel polls, no WebSockets)."""

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_GET, require_POST

from agent import loop
from agent.models import AgentAttachment, AgentRun, AgentStep

ALLOWED_TYPES = ('application/pdf', 'image/png', 'image/jpeg', 'image/webp', 'image/gif', 'image/heic', 'text/plain',
                 'text/csv')


def _error(message, status=400):
    return JsonResponse({'error': message}, status=status)


def undo_states(user, steps):
    """{undo id: 'available' | 'used' | 'expired'} for the steps' recorded changes (one query)."""
    from django.utils import timezone

    from console.models import UndoEntry
    ids = [s.undo_id for s in steps if s.undo_id]
    if not ids:
        return {}
    now = timezone.now()
    return {e.pk: ('used' if e.used_at else 'expired' if e.expires_at <= now else 'available')
            for e in UndoEntry.objects.filter(pk__in=ids, user=user).only('pk', 'used_at', 'expires_at')}


def step_json(step, undo=None):
    out = {'seq': step.seq, 'id': step.pk, 'kind': step.kind, 'tool': step.tool, 'text': step.text, 'ok': step.ok,
           'changed': step.changed, 'url': step.url, 'event_id': step.event_id, 'confirm': step.confirm_state,
           'at': step.created_at.isoformat()}
    if step.undo_id:
        state = (undo or {}).get(step.undo_id) if undo is not None else undo_states(step.run.user, [step]).get(step.undo_id)
        out['undo'] = {'id': step.undo_id, 'state': state or 'expired'}
    if step.kind == AgentStep.KIND_QUESTION:
        out['options'] = step.arguments.get('options') or []
    if step.kind == AgentStep.KIND_COMPONENT:  # the panel fetches the HTML (agent/components.py: render)
        out['component'] = {'name': step.arguments.get('component', ''), 'items': step.arguments.get('items') or [],
                            'picked': bool(step.arguments.get('picked'))}
    return out


def run_json(run):
    pending = run.steps.filter(confirm_state=AgentStep.CONFIRM_PENDING).order_by('-seq').first()
    question = (run.steps.filter(kind=AgentStep.KIND_QUESTION).order_by('-seq').first()
                if run.status == AgentRun.WAITING_REPLY else None)
    changed = list(run.steps.filter(changed=True).values('event_id', 'url'))
    return {
        'id': run.pk, 'status': run.status, 'active': run.active, 'goal': run.goal, 'summary': run.summary,
        'error': run.error, 'cost_usd': f'{run.cost_usd:.4f}', 'model_calls': run.step_count,
        'created_at': run.created_at.isoformat(),
        'pending': {'step': pending.pk, 'summary': pending.confirm_summary, 'hash': pending.confirm_hash,
                    'kind': pending.kind} if pending else None,
        'question': question.text if question else None,
        'options': (question.arguments.get('options') or []) if question else [],
        'undo': loop.undo_available(run), 'undone': run.undone_at is not None,
        'changed_events': sorted({c['event_id'] for c in changed if c['event_id']}),
        'changed_urls': sorted({c['url'] for c in changed if c['url']}),
    }


def _run(request, run_id):
    return get_object_or_404(AgentRun, pk=run_id, user=request.user)


@login_required
@require_POST
def start(request):
    from events.models import Event
    goal = (request.POST.get('goal') or '').strip()
    if not goal:
        return _error('Write what you want Attnd to do.')
    if len(goal) > 4000:
        return _error('That is too long (4,000 characters at most).')
    if AgentRun.objects.filter(user=request.user, status__in=AgentRun.ACTIVE).exists():
        active = AgentRun.objects.filter(user=request.user, status__in=AgentRun.ACTIVE).first()
        return JsonResponse({'error': 'Ask Attnd is still working on your last request. Stop it first, or answer it.',
                             'run': run_json(active)}, status=409)
    if loop.budget_left(request.user) <= 0:
        return _error(loop.budget_message(), status=429)
    page = (request.POST.get('page') or '').strip()
    if not page.startswith('/console/'):
        page = ''
    event = None
    if request.POST.get('event_id', '').isdigit():
        event = Event.objects.filter(pk=int(request.POST['event_id'])).first()
        if event is not None and not event.can_edit(request.user):
            event = None
    attachments = [int(i) for i in request.POST.get('attachments', '').split(',') if i.strip().isdigit()]
    if not request.session.session_key:
        request.session.save()
    run = loop.create_run(request, goal, page, event, attachments)
    return JsonResponse({'run': run_json(run), 'steps': []}, status=202)


@login_required
@require_GET
def runs(request):
    recent = AgentRun.objects.filter(user=request.user).order_by('-created_at')[:10]
    return JsonResponse({'runs': [{'id': r.pk, 'goal': r.goal[:200], 'status': r.status, 'active': r.active,
                                   'summary': (r.summary or r.error)[:300], 'created_at': r.created_at.isoformat()}
                                  for r in recent],
                         'spent_today': f'{loop.spent_today(request.user):.2f}',
                         'cap': f'{settings.AGENT_DAILY_SPEND_CAP_USD:.2f}'})


@login_required
@require_GET
def run(request, run_id):
    r = _run(request, run_id)
    after = int(request.GET.get('after') or 0) if str(request.GET.get('after') or '0').isdigit() else 0
    rows = list(r.steps.filter(seq__gt=after).order_by('seq')[:200])
    states = undo_states(request.user, rows)
    steps = [step_json(s, states) for s in rows]
    return JsonResponse({'run': run_json(r), 'steps': steps})


@login_required
@require_POST
def confirm(request, run_id):
    r = _run(request, run_id)
    try:
        step = loop.confirm(r, int(request.POST.get('step') or 0), request.POST.get('hash', ''))
    except loop.GateError as exc:
        return _error(str(exc), status=409)
    r.refresh_from_db()
    # Undo all and chat buttons ran right here: the panel shows their outcome (and refreshes components).
    done = step_json(step) if step.kind in (AgentStep.KIND_UNDO, AgentStep.KIND_BUTTON) else None
    return JsonResponse({'run': run_json(r), 'step': done})


@login_required
@require_POST
def cancel(request, run_id):
    r = _run(request, run_id)
    try:
        loop.cancel(r, int(request.POST.get('step') or 0))
    except loop.GateError as exc:
        return _error(str(exc), status=409)
    r.refresh_from_db()
    return JsonResponse({'run': run_json(r)})


@login_required
@require_POST
def reply(request, run_id):
    r = _run(request, run_id)
    text = (request.POST.get('text') or '').strip()
    if not text:
        return _error('Write a reply.')
    try:
        loop.reply(r, text)
    except loop.GateError as exc:
        return _error(str(exc), status=409)
    r.refresh_from_db()
    return JsonResponse({'run': run_json(r)})


@login_required
@require_POST
def stop(request, run_id):
    r = loop.stop(_run(request, run_id))
    return JsonResponse({'run': run_json(r)})


@login_required
@require_POST
def undo(request, run_id):
    r = _run(request, run_id)
    try:
        loop.request_undo(r)
    except loop.GateError as exc:
        return _error(str(exc), status=409)
    return JsonResponse({'run': run_json(r)})


@login_required
@require_POST
def attach(request):
    upload = request.FILES.get('file')
    if upload is None:
        return _error('Choose a file.')
    limit = settings.AGENT_MAX_ATTACHMENT_MB * 1024 * 1024
    if upload.size > limit:
        return _error(f'Files must be under {settings.AGENT_MAX_ATTACHMENT_MB} MB.')
    if (upload.content_type or '') not in ALLOWED_TYPES:
        return _error('Attach a PDF, an image (PNG, JPEG, WebP, GIF, HEIC) or a text/CSV file.')
    if AgentAttachment.objects.filter(user=request.user, run__isnull=True).count() >= 10:
        return _error('Send your request first (at most 10 files waiting).')
    attachment = AgentAttachment.objects.create(user=request.user, name=upload.name[:200],
                                                content_type=upload.content_type, size=upload.size,
                                                data=upload.read())
    return JsonResponse({'attachment': {'id': attachment.pk, 'name': attachment.name, 'size': attachment.size}},
                        status=201)
