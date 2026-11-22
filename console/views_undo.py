"""Undo / Redo for one recorded change (console/undo.py): the message's Undo button, the chat's Undo next to a step,
and Ask Attnd's "undo the last change"."""

from attnd.permissions import signed_in
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404, JsonResponse
from django.shortcuts import redirect
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from agent.registry import agent_action
from console import undo as U
from console.models import UndoEntry


def _wants_json(request):
    return 'application/json' in request.headers.get('Accept', '')


def _back(request):
    target = request.POST.get('next') or request.headers.get('Referer') or ''
    if target and url_has_allowed_host_and_scheme(target, {request.get_host()}, require_https=request.is_secure()):
        return target
    return '/console/'


def _answer(request, entry):
    if entry is None or entry.user_id != request.user.pk:
        raise Http404
    try:
        message, redo = U.undo(entry, request.user)
    except U.CannotUndo as exc:
        if _wants_json(request):
            return JsonResponse({'ok': False, 'error': str(exc)}, status=409)
        messages.error(request, str(exc))
        return redirect(_back(request))
    if _wants_json(request):
        return JsonResponse({'ok': True, 'message': message, 'redo': redo.pk if redo else None,
                             'event_id': entry.event_id})
    messages.success(request, message, extra_tags=f'redo-{redo.pk}' if redo else '')
    return redirect(_back(request))


@agent_action('undo_change', 'Undo one recorded change by its undo id (from a step or a page message). Refuses '
              'when it was sent to attendees already or someone changed the same thing again since.',
              title='Undid a change')
@signed_in
@require_POST
def undo_change(request, undo_id):
    return _answer(request, UndoEntry.objects.filter(pk=undo_id).first())


@agent_action('undo_last_change', 'Undo the person\'s most recent change in the console (theirs only; within 24 '
              'hours). Use it for "undo that" / "undo the last change".', title='Undid the last change')
@signed_in
@require_POST
def undo_last(request):
    entry = U.latest_for(request.user)
    if entry is None:
        if _wants_json(request):
            return JsonResponse({'ok': False, 'error': 'There is nothing to undo.'}, status=409)
        messages.info(request, 'There is nothing to undo.')
        return redirect(_back(request))
    return _answer(request, entry)
