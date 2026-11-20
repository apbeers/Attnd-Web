"""Console components in the Ask Attnd chat (console/components).

* The model's ``show(component, items)`` tool: the server validates the component and ids, loads the data as the
  person (the data functions check permissions like the views) and records a step ``{component, items}`` with the
  components' JSON as its result. The model gets that JSON, trimmed; the browser fetches the HTML, rendered from
  our templates as the person (``render``). Nothing stores HTML.
* ``get_component``: the JSON again without showing anything (for something shown earlier).
* The panel's endpoints: ``render`` (HTML for shown components; also to refresh stale copies), ``pick`` (a tap on a
  component shows its ``detail`` at once, no model call, and the run records the pick so the next message has it)
  and ``act`` (a component's always-ask button or form: it becomes a step waiting for Confirm, through the same gate
  as the model's own actions, ``loop.confirm``). Every other button and form in a chat component posts to its view
  directly (the browser's own request, with CSRF), exactly like the page.
"""

import json

from django.contrib.auth.decorators import login_required
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_GET, require_POST

from agent import loop, registry
from agent.models import AgentRun, AgentStep
from console import components as C

MAX_ITEMS = 12
FULL_JSON_COMPONENTS = 3  # earlier components the next request gets in full; older ones by name and ids only

ID_SCHEMA = {'type': 'object', 'additionalProperties': False, 'required': list(C.ID_NAMES),
             'properties': {k: {'type': ['integer', 'null']} for k in C.ID_NAMES}}


def tool_definitions():
    comps = [c for c in C.registry().values() if c.chat]
    catalog = '; '.join(f'{c.name} ({", ".join(c.ids) or "no ids"}'
                        f'{", optional " + ", ".join(c.optional_ids) if c.optional_ids else ""}): {c.description}'
                        for c in comps)
    names = [c.name for c in comps]
    return [
        {'name': 'show', 'description': 'Show console components to the person in the chat: live, interactive cards '
                                        'drawn by the console itself (tap for details, buttons and forms that work). '
                                        'Prefer this over describing these things in text. You get their data back. '
                                        f'Components: {catalog}',
         'parameters': {'type': 'object', 'additionalProperties': False, 'required': ['component', 'items'],
                        'properties': {
                            'component': {'type': 'string', 'enum': names},
                            'items': {'type': 'array', 'items': ID_SCHEMA,
                                      'description': f'one id set per card (unused ids null), at most {MAX_ITEMS}; a '
                                                     'list component with only its parent (e.g. event_id) shows '
                                                     'all of them; event_tile with no ids shows every event'}}},
         'strict': True},
        {'name': 'get_component', 'description': 'The current data of a component (e.g. one shown earlier) without '
                                                 'showing it again.',
         'parameters': {'type': 'object', 'additionalProperties': False, 'required': ['component', 'ids'],
                        'properties': {'component': {'type': 'string', 'enum': names}, 'ids': ID_SCHEMA}},
         'strict': True},
    ]


TOOL_NAMES = {'show', 'get_component'}


def _label(name, ids):
    return f'{name}({", ".join(f"{k}={v}" for k, v in ids.items())})'


def _load(user, name, items):
    """[(ids, data)] or raise ValueError with a message for the model."""
    if name not in C.registry() or not C.get(name).chat:
        raise ValueError(f'There is no component “{name}”.')
    try:
        found = C.instances(user, name, items)
    except C.IdError as exc:
        raise ValueError(str(exc)) from None
    except Http404:
        raise ValueError('Not found, or you don\'t have access to it.') from None
    if not found:
        raise ValueError('Nothing to show (none found).')
    return found[:MAX_ITEMS]


def show_tool(run, call, args):
    name = str(args.get('component') or '')
    items = args.get('items') if isinstance(args.get('items'), list) else [{}]
    try:
        found = _load(run.user, name, items[:MAX_ITEMS])
    except ValueError as exc:
        loop.add_step(run, tool='show', call_id=call.id, arguments={'component': name, 'items': items}, ok=False,
                      text=f'Couldn’t show {name}', result=str(exc))
        return f'Error: {exc}'
    title = C.get(name).title
    blobs = [C.model_json(data) for _, data in found]
    loop.add_step(run, kind=AgentStep.KIND_COMPONENT, tool='show', call_id=call.id,
                  arguments={'component': name, 'items': [ids for ids, _ in found], 'picked': False},
                  event_id=found[0][0].get('event_id'), text=f'{title} ×{len(found)}' if len(found) > 1 else title,
                  result='\n'.join(blobs)[:20000])
    return (f'Shown to the person ({len(found)} {title.lower()} card{"s" if len(found) != 1 else ""}; they see it, '
            'so don\'t repeat it as text). Their data:\n' + '\n'.join(blobs))


def get_tool(run, call, args):
    name = str(args.get('component') or '')
    ids = args.get('ids') if isinstance(args.get('ids'), dict) else {}
    try:
        found = _load(run.user, name, [ids])
    except ValueError as exc:
        return f'Error: {exc}'
    loop.add_step(run, tool='get_component', call_id=call.id, arguments={'component': name, 'ids': ids},
                  text=f'Looked at {C.get(name).title.lower()}')
    return '\n'.join(C.model_json(data) for _, data in found)


def earlier(run, runs):
    """Lines for the next request's context: components shown or picked in the earlier runs of this conversation.
    The latest few with their JSON as stored, older ones only by name and ids (get_component fetches them)."""
    steps = list(AgentStep.objects.filter(run__in=runs, kind=AgentStep.KIND_COMPONENT).order_by('-created_at')[:20])
    lines = []
    for n, step in enumerate(steps):
        name = step.arguments.get('component', '')
        items = step.arguments.get('items') or []
        who = 'The person opened' if step.arguments.get('picked') else 'You showed'
        label = ', '.join(_label(name, ids) for ids in items[:MAX_ITEMS])
        if n < FULL_JSON_COMPONENTS:
            lines.append(f'- {who} {label} — data: {step.result[:3000]}')
        else:
            lines.append(f'- {who} {label} (use get_component for its data)')
    return list(reversed(lines))


def picks_since(run, seq):
    """What the person opened in the chat after step `seq` (told to the model with their reply)."""
    picks = AgentStep.objects.filter(run=run, kind=AgentStep.KIND_COMPONENT, seq__gt=seq)
    return [f'{_label(p.arguments.get("component", ""), (p.arguments.get("items") or [{}])[0])}: {p.result[:2000]}'
            for p in picks if p.arguments.get('picked')]


# ---------------------------------------------------------------------------
# The panel's endpoints
# ---------------------------------------------------------------------------

def _error(message, status=400):
    return JsonResponse({'error': message}, status=status)


def _parse_items(raw):
    try:
        items = json.loads(raw or '[{}]')
    except ValueError:
        raise ValueError('items must be JSON.') from None
    if isinstance(items, dict):
        items = [items]
    if not isinstance(items, list) or not all(isinstance(i, dict) for i in items):
        raise ValueError('items must be a list of id sets.')
    return items[:MAX_ITEMS]


def rendered(request, name, found):
    """The chat's cards: HTML from our templates (as this person), with what the panel needs around it."""
    comp = C.get(name)
    out = []
    for ids, data in found:
        html = C.render(name, data, request, surface='chat', uid=C.new_uid())
        if name.endswith('_row'):
            html = f'<ul class="list list--boxed">{html}</ul>'
        out.append({'component': name, 'ids': ids, 'html': html, 'keys': C.keys(data), 'title': comp.title,
                    'label': data.get('name') or data.get('title') or comp.title,
                    'url': C.page_url(name, ids), 'detail': comp.detail})
    return out


@login_required
@require_GET
def render(request):
    """GET ?component=…&items=[{…}]: the HTML of components (shown by the model, or refreshing a stale copy)."""
    name = request.GET.get('component', '')
    try:
        found = _load(request.user, name, _parse_items(request.GET.get('items')))
    except ValueError as exc:
        return _error(str(exc), status=404 if 'access' in str(exc) or 'none found' in str(exc) else 400)
    return JsonResponse({'items': rendered(request, name, found)})


def _own_run(request):
    raw = request.POST.get('run') or ''
    if not raw.isdigit():
        raise Http404
    return get_object_or_404(AgentRun, pk=int(raw), user=request.user)


@login_required
@require_POST
def pick(request):
    """The person tapped a component: show its detail now (no model call) and remember it in the run."""
    from agent.views import step_json
    run = _own_run(request)
    name = request.POST.get('component', '')
    try:
        (tapped,) = _parse_items(request.POST.get('ids') or '{}')[:1]
        source = C.get(name)
        tapped = C.clean_ids(source, tapped)  # the tapped card's own ids
    except (ValueError, KeyError):
        return _error('Unknown component or ids.')
    if not source.detail:
        return _error('This has no detail view.')
    detail = C.get(source.detail)
    try:
        found = _load(request.user, detail.name, [{k: v for k, v in tapped.items() if k in detail.all_ids}])
    except ValueError as exc:
        return _error(str(exc), status=404)
    ids, data = found[0]
    step = loop.add_step(run, kind=AgentStep.KIND_COMPONENT, tool='pick',
                         arguments={'component': detail.name, 'items': [ids], 'picked': True, 'from': name},
                         event_id=ids.get('event_id'), text=f'You opened {data.get("name") or data.get("title") or detail.title}',
                         result=C.model_json(data))
    return JsonResponse({'step': step_json(step), 'items': rendered(request, detail.name, found)})


def _tool_args(action, ids, post):
    """A gated form's values as the action's tool arguments (what the agent would pass)."""
    from django import forms as dj_forms
    args = {name: ids[name] for name, _ in action.kwargs}
    if action.form:
        fields = registry.form_fields(action.form)
        for name in registry.form_field_names(action):
            f = fields[name]
            if isinstance(f, dj_forms.BooleanField):
                args[name] = bool(post.get(name))
            else:
                value = post.get(name, '')
                args[name] = value if value != '' else None
    return args


@login_required
@require_POST
def act(request):
    """A component's always-ask button (or form) in the chat: a step waiting for Confirm, like the model's own.

    Only actions the component offers right now and that are always-ask; nothing runs here. Confirm goes through
    agent:confirm (loop.confirm: single use, the hash of exactly these arguments)."""
    from agent.internal import ActionContext
    from agent.views import run_json, step_json
    run = _own_run(request)
    name = request.POST.get('component', '')
    if name not in C.registry():
        return _error('Unknown component.')
    comp = C.get(name)
    try:
        (ids,) = _parse_items(request.POST.get('ids') or '{}')[:1]
        ids = C.clean_ids(comp, ids)
        data = C.load(request.user, name, ids)
    except (ValueError, C.IdError) as exc:
        return _error(str(exc))
    except Http404:
        return _error('Not found, or you don\'t have access to it.', status=404)
    action_name = request.POST.get('action', '')
    offered = {a['name']: a for a in data.get('actions', [])}
    if action_name not in offered:
        return _error('That button isn\'t available here (any more).', status=409)
    action = registry.writes_for(request.user).get(action_name)
    if action is None or not action.confirm:
        return _error('That action doesn\'t need a confirmation: post it to its page.', status=400)
    if comp.form:  # a form that always asks (e.g. sending an update): check it first, errors come back in place
        form = comp.form(request.user, ids, request.POST)
        if not form.is_valid():
            again = C.finish(comp, ids, comp.data(request.user, **ids, form=form))
            return JsonResponse({'invalid': True, 'items': rendered(request, name, [(ids, again)])})
    if run.steps.filter(confirm_state=AgentStep.CONFIRM_PENDING).exists():
        return _error('Answer the confirmation that is waiting first.', status=409)
    args = _tool_args(action, ids, request.POST)
    ctx = ActionContext(run, action, args)
    summary = action.summary(ctx) if action.summary else loop._default_summary(ctx)
    # The person's current session runs it (they are signed in right now).
    if request.session.session_key:
        AgentRun.objects.filter(pk=run.pk).update(session_key=request.session.session_key, host=request.get_host(),
                                                  secure=request.is_secure())
    step = loop.add_step(run, kind=AgentStep.KIND_BUTTON, tool=action.name, arguments=args,
                         event_id=args.get('event_id'), text=f'Waiting for your confirmation: {summary}',
                         confirm_state=AgentStep.CONFIRM_PENDING, confirm_summary=summary,
                         confirm_hash=loop.args_hash(action.name, args))
    run.refresh_from_db()
    return JsonResponse({'run': run_json(run), 'step': step_json(step)})
