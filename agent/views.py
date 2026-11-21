"""Ask Attnd's endpoints for the console panel (JSON; the panel polls, no WebSockets)."""

import json
import re

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_GET, require_POST

from agent import loop
from agent.components import BUTTONS_GOAL
from agent.models import AgentAttachment, AgentRun, AgentStep

ALLOWED_TYPES = ('application/pdf', 'image/png', 'image/jpeg', 'image/webp', 'image/gif', 'image/heic', 'text/plain',
                 'text/csv')


def _error(message, status=400):
    return JsonResponse({'error': message}, status=status)


VERBS = {'add': 'Adding', 'edit': 'Editing', 'delete': 'Deleting', 'send': 'Sending', 'update': 'Updating',
         'publish': 'Publishing', 'unpublish': 'Unpublishing', 'move': 'Moving', 'create': 'Creating', 'invite': 'Inviting',
         'remove': 'Removing', 'set': 'Setting', 'undo': 'Undoing', 'schedule': 'Scheduling', 'save': 'Saving',
         'archive': 'Archiving', 'restore': 'Restoring', 'reorder': 'Reordering', 'hide': 'Hiding', 'apply': 'Applying',
         'discard': 'Discarding', 'change': 'Changing', 'leave': 'Leaving', 'transfer': 'Transferring', 'check': 'Checking',
         'import': 'Importing', 'bring': 'Bringing', 'unlock': 'Unlocking', 'toggle': 'Switching', 'list': 'Listing'}
PAGE_NAMES = {'': 'the overview', 'edit': 'Details', 'locations': 'Locations', 'schedule': 'the schedule',
              'notifications': 'Updates', 'display': 'App layout', 'changes': 'AI updates', 'usage': 'Usage',
              'history': 'History', 'team': 'Team', 'studio': 'Studio'}


def _page_name(path):
    parts = [p for p in (path or '').split('/') if p]
    if parts[:2] == ['console', 'events'] and len(parts) >= 3:
        rest = [p for p in parts[3:] if not p.isdigit()]
        return PAGE_NAMES.get(rest[0] if rest else '', (rest[-1] if rest else 'the event').replace('_', ' ').capitalize())
    if parts == ['console']:
        return 'your events'
    return (parts[-1] if parts else 'the page').replace('-', ' ').replace('_', ' ').capitalize()


def stage_text(step):
    """The thinking line while a step runs: "Reading Notifications…", "Adding location “Taco Row”…"."""
    args = step.arguments or {}
    if step.kind == AgentStep.KIND_COMPONENT:
        return f'Showing {step.text}…'
    if step.tool == 'read_page':
        return f'Reading {_page_name(args.get("path"))}…'
    if step.tool in ('list_events', 'search_events'):
        return 'Looking through your events…'
    if step.tool == 'list_pages':
        return 'Finding the right page…'
    if step.confirm_state == AgentStep.CONFIRM_PENDING:
        return 'Waiting for you to confirm…'
    if step.kind == AgentStep.KIND_QUESTION:
        return 'Waiting for your answer…'
    words = (step.tool or '').split('_')
    if not words or not words[0]:
        return 'Working…'
    verb = VERBS.get(words[0])
    what = ' '.join(words[1:] if verb else words)
    named = next((str(args[k]) for k in ('name', 'title') if args.get(k)), '')
    return f'{verb or "Working on"} {what}{f" “{named[:40]}”" if named else ""}…'


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


LABEL_MAX = 80


def step_label(step):
    """A step's one-line label in the chat: never page text or JSON (A9). Notes, questions and replies are the
    model's or the person's own words and stay whole; tool steps are cut to one line of ~80 characters."""
    text = step.text or ''
    if step.kind in (AgentStep.KIND_NOTE, AgentStep.KIND_QUESTION, AgentStep.KIND_REPLY):
        return text
    text = text.split('\n', 1)[0]
    cut = re.search(r'[\[{<]', text)  # anything that looks like data starts a dump: drop it
    if cut and cut.start() > 0:
        text = text[:cut.start()].rstrip(' :·—-')
    text = ' '.join(text.split())
    if len(text) > LABEL_MAX:
        text = text[:LABEL_MAX - 1].rstrip() + '…'
    return text


def step_json(step, undo=None):
    out = {'seq': step.seq, 'id': step.pk, 'kind': step.kind, 'tool': step.tool, 'text': step_label(step), 'ok': step.ok,
           'changed': step.changed, 'url': step.url, 'event_id': step.event_id, 'confirm': step.confirm_state,
           'at': step.created_at.isoformat(), 'stage': stage_text(step)}
    if step.undo_id:
        state = (undo or {}).get(step.undo_id) if undo is not None else undo_states(step.run.user, [step]).get(step.undo_id)
        out['undo'] = {'id': step.undo_id, 'state': state or 'expired'}
    if step.kind == AgentStep.KIND_QUESTION:
        out['options'] = step.arguments.get('options') or []
    if step.kind == AgentStep.KIND_COMPONENT:  # the panel fetches the HTML (agent/components.py: render)
        out['component'] = {'name': step.arguments.get('component', ''), 'items': step.arguments.get('items') or [],
                            'picked': bool(step.arguments.get('picked'))}
        if step.tool == 'fill_form':  # the panel puts these values into that form on screen
            out['component']['fill'] = step.arguments.get('fill') or {}
            out['component']['submit'] = bool(step.arguments.get('submit'))
    return out


# Follow-ups when the model gave none: from the last component the run showed (the panel's chips).
FOLLOW_UPS = {
    'event_tile': ['Show the overview', 'Create an event'],
    'event_overview': ['Show the schedule', 'Show locations', 'Send a notification'],
    'location_row': ['Add a location', 'Show the schedule', 'Send a notification'],
    'location_form': ['Show locations', 'Show the schedule'],
    'schedule_item_row': ['Add a schedule item', 'Show locations', 'Send a notification'],
    'schedule_item_form': ['Show the schedule', 'Send a notification'],
    'notification_row': ['Send a notification', 'Show the schedule'],
    'notification_form': ['Show notifications', 'Show the schedule'],
    'event_team': ['Invite someone', 'Show the metrics'],
    'event_metrics': ['Show the most liked', 'Send a notification'],
}


def suggestions(run):
    if run.status != AgentRun.DONE:
        return []
    if run.suggestions:
        return run.suggestions[:4]
    last = run.steps.filter(kind=AgentStep.KIND_COMPONENT).order_by('-seq').first()
    if last is not None:
        return FOLLOW_UPS.get(last.arguments.get('component', ''), [])[:4]
    return ['Show the schedule', 'Add a location', 'Send a notification'] if run.event_id else []


def run_json(run):
    pending = run.steps.filter(confirm_state=AgentStep.CONFIRM_PENDING).order_by('-seq').first()
    question = (run.steps.filter(kind=AgentStep.KIND_QUESTION).order_by('-seq').first()
                if run.status == AgentRun.WAITING_REPLY else None)
    changed = list(run.steps.filter(changed=True).values('event_id', 'url'))
    return {
        'id': run.pk, 'status': run.status, 'active': run.active, 'goal': run.goal, 'summary': run.summary,
        'buttons': run.goal == BUTTONS_GOAL,  # a quiet run that only holds chat buttons' confirmations
        'error': run.error, 'cost_usd': f'{run.cost_usd:.4f}', 'model_calls': run.step_count,
        'created_at': run.created_at.isoformat(),
        'pending': {'step': pending.pk, 'summary': pending.confirm_summary, 'hash': pending.confirm_hash,
                    'kind': pending.kind} if pending else None,
        'question': question.text if question else None,
        'options': (question.arguments.get('options') or []) if question else [],
        'undo': loop.undo_available(run), 'undone': run.undone_at is not None,
        'suggestions': suggestions(run),
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
    target = {}
    if request.POST.get('target'):  # Add with AI: the form on screen the agent fills (checked when it fills it)
        try:
            raw = json.loads(request.POST['target'])
            target = {'component': str(raw.get('component', ''))[:64],
                      'ids': {k: int(v) for k, v in (raw.get('ids') or {}).items() if str(v).isdigit()}}
        except (ValueError, AttributeError, TypeError):
            target = {}
    run = loop.create_run(request, goal, page, event, attachments, target=target)
    return JsonResponse({'run': run_json(run), 'steps': []}, status=202)


@login_required
@require_GET
def runs(request):
    recent = list(AgentRun.objects.filter(user=request.user).order_by('-created_at')[:10])
    out = {'runs': [{'id': r.pk, 'goal': r.goal[:200], 'status': r.status, 'active': r.active,
                     'summary': (r.summary or r.error)[:300], 'created_at': r.created_at.isoformat()}
                    for r in recent],
           'spent_today': f'{loop.spent_today(request.user):.2f}',
           'cap': f'{settings.AGENT_DAILY_SPEND_CAP_USD:.2f}'}
    if 'chat' in request.GET:
        # The conversation on reload, in one request (A21): the runs of this chat (?chat=3,5,8) plus an active one,
        # each with its steps, oldest first.
        wanted = [int(i) for i in request.GET['chat'].split(',') if i.strip().isdigit()][-20:]
        active = next((r.pk for r in recent if r.active), None)
        if active and active not in wanted:
            wanted.append(active)
        found = {r.pk: r for r in AgentRun.objects.filter(user=request.user, pk__in=wanted)}
        chat = []
        for pk in wanted:
            r = found.get(pk)
            if r is None:
                continue
            rows = list(r.steps.order_by('seq')[:200])
            states = undo_states(request.user, rows)
            chat.append({'run': run_json(r), 'steps': [step_json(s, states) for s in rows]})
        out['chat'] = chat
    return JsonResponse(out)


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


@login_required
@require_GET
def assistant_page(request):
    """Ask Attnd full screen (desktop): the chat on the left, a canvas of what it shows on the right, details as an
    overlay. Same conversation as the floating panel (sessionStorage); ?event= the event it is about, ?from= the
    page to go back to (and the page the requests are about)."""
    from django.shortcuts import render
    from django.utils.http import url_has_allowed_host_and_scheme

    from agent.registry import off_limits
    from events.models import Event
    event = None
    raw = request.GET.get('event', '')
    if raw.isdigit():
        event = Event.objects.filter(pk=int(raw)).first()
        if event is not None and not event.can_edit(request.user):
            event = None
    back = request.GET.get('from', '')
    if not (back.startswith('/console/') and not off_limits(back)
            and url_has_allowed_host_and_scheme(back, {request.get_host()})):
        back = '/console/'
    from console.models import ConsolePreference
    if ConsolePreference.chat_first(request.user) and 'page=1' not in back:
        back += ('&' if '?' in back else '?') + 'page=1'  # Exit goes to the page (chat stays the default)
    from agent import canvas
    return render(request, 'agent/assistant.html', {'agent_event': event, 'back_url': back,
                                                    'canvas_tabs': canvas.build(request, event),
                                                    'canvas_routes': canvas.routes_json()})
