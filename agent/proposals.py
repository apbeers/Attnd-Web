"""Preview before apply (stage I, Tier 1 item 1).

For requests that change several things at once ("move all Saturday sets 30 min later", "tag every food truck
'food'", "hide the parking spots") the model calls `propose_changes` instead of the writes themselves. Each change is
a registered write action with its arguments (always-ask actions are refused: they keep their own confirm card); the
server reads each form's current values (the same prefill a save does) and keeps old → new per field. The chat shows
the `change_preview` card: every row ticked, untick any, Apply / Cancel. Apply runs the ticked ones through the real
views as the person (`internal.execute`) inside one undo recording, so one Undo reverses them all.
"""

import json

from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from agent import internal, registry
from agent.models import AgentStep, ChangeProposal
from console.components import component

MAX_CHANGES = 40

TOOL = {
    'name': 'propose_changes',
    'description': 'Preview several changes at once for the person to check and apply (old → new per row, they can '
                   'untick any). Use it instead of the write tools whenever a request changes 2 or more things '
                   '(bulk edits, shifting times, tagging, hiding). Each change is one write tool with its arguments. '
                   'Not for sending, publishing or deleting (those always ask on their own).',
    'parameters': {'type': 'object', 'additionalProperties': False, 'required': ['event_id', 'title', 'changes'],
                   'properties': {
                       'event_id': {'type': 'integer'},
                       'title': {'type': 'string', 'description': 'what the whole change does, e.g. "Move Saturday '
                                                                   'sets 30 minutes later"'},
                       'changes': {'type': 'array', 'items': {
                           'type': 'object', 'additionalProperties': False,
                           'required': ['tool', 'arguments_json', 'label'],
                           'properties': {
                               'tool': {'type': 'string', 'description': 'a write tool name, e.g. edit_schedule_item'},
                               'arguments_json': {'type': 'string', 'description': 'that tool\'s arguments as a JSON '
                                                                                   'object'},
                               'label': {'type': 'string', 'description': 'the row, e.g. "Prairie Lights"'}}}}}},
    'strict': True,
}


def _rows(action, args, before):
    prefix = f'{action.prefix}-' if action.prefix else ''
    rows = []
    for name in registry.form_field_names(action):
        value = args.get(name)
        if value is None:
            continue
        new = ('on' if value else '') if isinstance(value, bool) else value
        old = before.get(prefix + name, '')
        if isinstance(old, list):
            old = ', '.join(str(o) for o in old)
        if isinstance(new, list):
            new = ', '.join(str(n) for n in new)
        if str(old or '') == str(new or ''):
            continue
        rows.append([name.replace('_', ' '), str(old or '—')[:80], str(new if new != '' else '—')[:80]])
    return rows


def propose_tool(run, call, args):
    from agent import loop
    from events.models import Event
    event_id = args.get('event_id')
    event = Event.objects.filter(pk=event_id).first() if isinstance(event_id, int) else None
    if event is None or not event.can_edit(run.user):
        return 'Error: no such event, or the person can\'t edit it.'
    raw = args.get('changes') if isinstance(args.get('changes'), list) else []
    if not raw:
        return 'Error: no changes given.'
    if len(raw) > MAX_CHANGES:
        return f'Error: at most {MAX_CHANGES} changes in one preview; split the request.'
    writes = registry.writes_for(run.user)
    changes, problems = [], []
    for n, change in enumerate(raw, 1):
        name = str(change.get('tool') or '')
        action = writes.get(name)
        if action is None:
            problems.append(f'change {n}: unknown tool {name}')
            continue
        if action.confirm:
            problems.append(f'change {n}: {name} always asks on its own; call it directly instead')
            continue
        try:
            targs = json.loads(change.get('arguments_json') or '{}')
        except ValueError:
            problems.append(f'change {n}: arguments_json is not JSON')
            continue
        if not isinstance(targs, dict):
            problems.append(f'change {n}: arguments_json must be an object')
            continue
        keys = set(registry.tool_schema(action)['properties'])
        targs = {k: v for k, v in targs.items() if k in keys}
        if targs.get('event_id', event.pk) != event.pk:
            problems.append(f'change {n}: another event')
            continue
        targs['event_id'] = event.pk
        try:
            _, _, _, before = internal.build_data(run, action, targs)
        except ValueError as exc:
            problems.append(f'change {n}: {exc}')
            continue
        rows = _rows(action, targs, before)
        if action.form and not rows:
            continue  # nothing would change
        changes.append({'action': name, 'args': targs, 'label': str(change.get('label') or action.label())[:120],
                        'rows': rows})
    if problems:
        return 'Error, nothing previewed:\n' + '\n'.join(problems)
    if not changes:
        return 'Nothing would change: everything already has those values.'
    proposal = ChangeProposal.objects.create(run=run, event=event, title=str(args.get('title') or 'Changes')[:200],
                                             changes=changes)
    loop.add_step(run, kind=AgentStep.KIND_COMPONENT, tool='propose_changes', call_id=call.id, event_id=event.pk,
                  arguments={'component': 'change_preview', 'items': [{'event_id': event.pk,
                                                                      'proposal_id': proposal.pk}], 'picked': False},
                  text=f'Preview: {proposal.title} ({len(changes)} change{"s" if len(changes) != 1 else ""})',
                  result=json.dumps({'proposal': proposal.pk, 'changes': len(changes)}))
    return (f'Preview shown with {len(changes)} change(s); nothing is changed yet. The person applies or cancels it '
            'on the card. Finish now with one short sentence (don\'t list the changes again).')


@component('change_preview', 'Change preview', 'Several changes Ask Attnd proposed, old → new per row; the person '
           'unticks any and applies them in one go (one Undo), or cancels.',
           ids=('event_id', 'proposal_id'), chat_only='Ask Attnd\'s proposals exist only in the chat.')
def change_preview(user, event_id=None, proposal_id=None):
    proposal = ChangeProposal.objects.select_related('event').filter(pk=proposal_id, event_id=event_id,
                                                                    run__user=user).first()
    if proposal is None:
        raise Http404
    results = {r['index']: r for r in proposal.results or []}
    rows = [{'index': i, 'label': c['label'], 'rows': c.get('rows') or [],
             'action': registry.registry()['writes'][c['action']].label() if c['action'] in registry.registry()['writes']
             else c['action'], 'result': results.get(i)} for i, c in enumerate(proposal.changes)]
    return {'id': proposal.pk, 'event_id': proposal.event_id, 'name': proposal.title, 'status': proposal.status,
            'rows': rows, 'count': len(rows), 'undo': proposal.undo_id,
            'url': reverse('agent:proposal_apply', args=[proposal.pk]),
            'applied': sum(1 for r in proposal.results or [] if r.get('ok'))}


@login_required
@require_POST
def apply(request, proposal_id):
    """Apply the ticked rows (POST `keep` = row indexes) or cancel (`cancel`). JSON like a component form's save."""
    from console.undo import finalize, recording
    proposal = get_object_or_404(ChangeProposal.objects.select_related('run', 'event'), pk=proposal_id,
                                 run__user=request.user)
    if proposal.status != ChangeProposal.PENDING:
        return JsonResponse({'saved': False, 'error': 'This preview was already applied or cancelled.'}, status=409)
    if not proposal.event.can_edit(request.user):
        raise Http404
    if request.POST.get('cancel'):
        proposal.status, proposal.decided_at = ChangeProposal.CANCELLED, timezone.now()
        proposal.save(update_fields=['status', 'decided_at'])
        return JsonResponse({'saved': True, 'messages': ['Cancelled: nothing changed.']})
    keep = {int(i) for i in request.POST.getlist('keep') if str(i).isdigit()}
    writes = registry.writes_for(request.user)
    results = []
    with recording(request.user) as rec:  # one undo entry for every row (inner requests join this recording)
        for i, change in enumerate(proposal.changes):
            if i not in keep:
                continue
            action = writes.get(change['action'])
            if action is None or action.confirm:
                results.append({'index': i, 'ok': False, 'text': 'Not allowed.'})
                continue
            try:
                with transaction.atomic():
                    out = internal.execute(proposal.run, action, change['args'])
            except ValueError as exc:
                out = {'ok': False, 'result': str(exc)}
            results.append({'index': i, 'ok': bool(out.get('ok')),
                            'text': '' if out.get('ok') else str(out.get('result') or '')[:200]})
    applied = sum(1 for r in results if r['ok'])
    entry = finalize(rec, f'Applied “{proposal.title}” ({applied} change{"s" if applied != 1 else ""})', via='chat')
    proposal.status, proposal.decided_at = ChangeProposal.APPLIED, timezone.now()
    proposal.results, proposal.undo_id = results, entry.pk if entry else None
    proposal.save(update_fields=['status', 'decided_at', 'results', 'undo_id'])
    failed = len(results) - applied
    text = f'Applied {applied} change{"s" if applied != 1 else ""}' + (f'; {failed} didn’t work' if failed else '') + '.'
    return JsonResponse({'saved': True, 'messages': [text], 'undo': entry.pk if entry else None})
