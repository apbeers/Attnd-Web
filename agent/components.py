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


def _phrase(comp):
    """A component in a phrase for the show tool (the full descriptions cost thousands of tokens per call)."""
    text = comp.description.split(':')[0].split('. ')[0]
    return text if len(text) <= 48 else text[:45].rstrip() + '…'


def tool_definitions(user=None):
    comps = [c for c in C.registry().values() if c.chat and (user is None or C.allowed(c, user))]
    catalog = '; '.join(f'{c.name} ({", ".join(c.ids) or "no ids"}'
                        f'{", optional " + ", ".join(c.optional_ids) if c.optional_ids else ""}): {_phrase(c)}'
                        for c in comps)
    names = [c.name for c in comps]
    return [
        {'name': 'show', 'description': 'Show console components (live cards: tap for details, working buttons and '
                                        'forms); prefer this to describing them. Returns their data. '
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
        FILL_TOOL,
    ]


FILL_TOOL = {
    'name': 'fill_form', 'description': 'Fill a console form in front of the person (the form component on screen, '
    'shown in the chat or on the canvas if it isn\'t yet): the values appear highlighted, NOTHING is saved until the '
    'person presses Save, or you pass submit true when they said "save it". Use the form\'s field names (from '
    'get_component). Details for the assistant (ctx_ fields) are only filled when empty.',
    'parameters': {'type': 'object', 'additionalProperties': False, 'required': ['component', 'ids', 'values', 'submit'],
                   'properties': {
                       'component': {'type': 'string'},
                       'ids': ID_SCHEMA,
                       'values': {'type': 'array', 'description': 'field name and its new value as text ("true" / '
                                  '"false" for checkboxes; YYYY-MM-DDTHH:MM for times)',
                                  'items': {'type': 'object', 'additionalProperties': False, 'required': ['name', 'value'],
                                            'properties': {'name': {'type': 'string'}, 'value': {'type': 'string'}}}},
                       'submit': {'type': 'boolean', 'description': 'press Save after filling (only when the person '
                                                                   'asked to save)'}}},
    'strict': True}

TOOL_NAMES = {'show', 'get_component', 'fill_form'}


def form_components():
    """Components whose data has a form (each offers Add with AI and can be filled by fill_form)."""
    return [c for c in C.registry().values() if c.chat and c.name.endswith('_form')]


def fill_tool(run, call, args):
    """Validate a fill against the form the person sees and record it; the panel applies it to that form."""
    name = str(args.get('component') or '')
    ids = args.get('ids') if isinstance(args.get('ids'), dict) else {}
    try:
        found = _load(run.user, name, [ids])
    except ValueError as exc:
        return f'Error: {exc}'
    ids, data = found[0]
    forms = [f for f in C.forms_of(data) if not f.get('background')]
    if not forms:
        return f'Error: {name} has no form to fill.'
    fields = {k: v for f in forms for k, v in f['fields'].items()}
    by_name = {}
    for key, f in fields.items():
        by_name[f['name']] = f
        by_name[key] = f
    locked_context = any(f.get('locked') for k, f in fields.items() if k.startswith('ctx_'))
    values, skipped = {}, []
    for pair in (args.get('values') or [])[:60]:
        if not isinstance(pair, dict):
            continue
        field = by_name.get(str(pair.get('name') or ''))
        value = str(pair.get('value') if pair.get('value') is not None else '')[:8000]
        if field is None or field['widget'] == 'file':
            skipped.append(str(pair.get('name'))[:40])
            continue
        short = field['name'].split('-')[-1]
        if short.startswith('ctx_') and (locked_context or (field.get('value') or '').strip()):
            skipped.append(f'{short} (already filled or edited by hand)')
            continue
        if field['widget'] in ('select', 'radio'):
            options = {o['value']: o for o in field.get('options') or []}
            match = options.get(value) or next((o for o in options.values() if o['label'].lower() == value.lower()), None)
            if match is None:
                skipped.append(f'{short} (not one of {", ".join(o["label"] for o in options.values())[:120]})')
                continue
            value = match['value']
        values[field['name']] = value[:int(field['attrs'].get('maxlength') or 8000)]
    if not values:
        return ('Error: nothing to fill. Fields: ' + ', '.join(f.split('-')[-1] for f in list(fields)[:40])
                + (f'. Skipped: {", ".join(skipped)}' if skipped else ''))
    submit = bool(args.get('submit'))
    # Forms that always ask (sending a notification, a team invite) are never submitted by the agent: the person
    # presses Send there, which goes through the confirm card (the audit's confirm-gate fix).
    gated = [f.get('confirm_action') for f in forms if f.get('confirm_action')]
    refused_submit = submit and bool(gated)
    if refused_submit:
        submit = False
    title = C.get(name).title
    loop.add_step(run, kind=AgentStep.KIND_COMPONENT, tool='fill_form', call_id=call.id,
                  arguments={'component': name, 'items': [ids], 'picked': False, 'fill': values, 'submit': submit},
                  event_id=ids.get('event_id'),
                  text=f'{"Filled and saved" if submit else "Filled"} {len(values)} field{"s" if len(values) != 1 else ""} '
                       f'in {data.get("title") or title}', result=json.dumps(values, ensure_ascii=False)[:4000])
    note = (' It is being saved now (the page answers with any errors).' if submit else
            ' Nothing is saved: the person reviews it and presses Save, or tells you to save it.')
    if refused_submit:
        note = (' NOT submitted: this form always asks the person to confirm. Tell them to check it and press '
                f'{"Send" if "send" in gated[0] else "the button"} themselves (it shows the confirmation).')
    return (f'Filled {", ".join(k.split("-")[-1] for k in values)} in the {title.lower()} on screen.{note}'
            + (f' Skipped: {", ".join(skipped)}.' if skipped else ''))


def _label(name, ids):
    return f'{name}({", ".join(f"{k}={v}" for k, v in ids.items())})'


RENDER_LIMIT = 60  # cards per render request (the canvas pages with "Show more"); the model's show keeps 12


def _load(user, name, items, limit=MAX_ITEMS, start=0, with_total=False):
    """[(ids, data)] or raise ValueError with a message for the model."""
    if name not in C.registry() or not C.get(name).chat or not C.allowed(C.get(name), user):
        raise ValueError(f'There is no component “{name}”.')
    try:
        found, total = C.instances_page(user, name, items, start, limit)
    except C.IdError as exc:
        raise ValueError(str(exc)) from None
    except Http404:
        raise ValueError('Not found, or you don\'t have access to it.') from None
    if not found and not start:
        raise ValueError('Nothing to show (none found).')
    return (found, total) if with_total else found


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
    return items[:RENDER_LIMIT]


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
                    'url': C.page_url(name, ids), 'detail': comp.detail, 'group': data.get('group', ''),
                    'detail_ids': list(C.get(comp.detail).all_ids) if comp.detail else []})
    return out


@login_required
@require_GET
def render(request):
    """GET ?component=…&items=[{…}]: the HTML of components (shown by the model, or refreshing a stale copy)."""
    name = request.GET.get('component', '')
    offset = request.GET.get('offset', '0')
    start = int(offset) if offset.isdigit() else 0
    try:
        found, total = _load(request.user, name, _parse_items(request.GET.get('items')), limit=RENDER_LIMIT,
                             start=start, with_total=True)
    except ValueError as exc:
        return _error(str(exc), status=404 if 'access' in str(exc) or 'none found' in str(exc) else 400)
    end = start + len(found)
    return JsonResponse({'items': rendered(request, name, found), 'total': total,
                         'next_offset': end if end < total else None})


@login_required
@require_GET
def batch(request):
    """GET ?q=[{"component": …, "items": [{…}]}, …]: several components in one request (the canvas's live numbers
    and refreshes). Each entry answers like render (items) or with its error; at most 20 entries."""
    try:
        wanted = json.loads(request.GET.get('q') or '[]')
    except ValueError:
        return _error('q must be JSON.')
    if not isinstance(wanted, list):
        return _error('q must be a list.')
    out = []
    for entry in wanted[:20]:
        if not isinstance(entry, dict):
            out.append({'error': 'Bad entry.'})
            continue
        name = str(entry.get('component') or '')
        try:
            found = _load(request.user, name, _parse_items(json.dumps(entry.get('items') or [{}])), limit=RENDER_LIMIT)
            out.append({'component': name, 'items': rendered(request, name, found)})
        except ValueError as exc:
            out.append({'component': name, 'error': str(exc)})
    return JsonResponse({'results': out})


BUTTONS_GOAL = 'Buttons in the chat'


def _own_run(request, create=False):
    """The run a tap belongs to. With `create` (always-ask buttons), a chat that has no request yet (the full-screen
    canvas before anything was asked) gets a quiet run of its own to hold the confirmation: no model call."""
    raw = request.POST.get('run') or ''
    if not raw.isdigit():
        if not create:
            raise Http404
        from attnd.ratelimit import RateLimit
        limit = RateLimit('agent_quiet_runs')
        if limit.hit(request.user.pk) > limit.limit:
            raise PermissionError('Too many chat buttons in a short time. Wait a few minutes and try again.')
        return AgentRun.objects.create(user=request.user, goal=BUTTONS_GOAL, status=AgentRun.DONE,
                                       summary='', session_key=request.session.session_key or '',
                                       thread=loop.thread_for(request),
                                       host=request.get_host(), secure=request.is_secure())
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


def _tool_args(action, ids, post, alias=None):
    """A gated form's values as the action's tool arguments (what the agent would pass)."""
    from django import forms as dj_forms
    alias = alias or {}
    args = {name: ids[name] if name in ids else ids[alias[name]] for name, _ in action.kwargs}
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
    try:
        posted = {str(k): str(v) for k, v in json.loads(request.POST.get('fields') or '{}').items()}
    except (ValueError, AttributeError):
        posted = None
    # The very button offered now: the same action and the same values (remove *this* member, not another one)
    offered = [a for a in data.get('actions', []) if a['name'] == action_name and a.get('params', {}) == posted]
    if not offered:
        return _error('That button isn\'t available here (any more).', status=409)
    action = registry.writes_for(request.user).get(action_name)
    if action is None or not action.confirm:
        return _error('That action doesn\'t need a confirmation: post it to its page.', status=400)
    gated = {f.get('confirm_action') for f in C.forms_of(data)}
    if comp.form and action_name in gated:  # a form that always asks (e.g. sending an update): check it first, errors come back in place
        form = comp.form(request.user, ids, request.POST)
        if not form.is_valid():
            again = C.finish(comp, ids, comp.data(request.user, **ids, form=form))
            return JsonResponse({'invalid': True, 'items': rendered(request, name, [(ids, again)])})
    try:
        run = _own_run(request, create=True)
    except PermissionError as exc:
        return _error(str(exc), status=429)
    if run.steps.filter(confirm_state=AgentStep.CONFIRM_PENDING).exists():
        return _error('Answer the confirmation that is waiting first.', status=409)
    args = _tool_args(action, ids, request.POST, comp.kwarg_map)
    if comp.act_args:  # e.g. the review's ticked changes → accept_change_ids
        args.update(comp.act_args(action_name, request.POST))
    for name, value in offered[0].get('params', {}).items():  # the button's own values, typed like the tool's
        if name in action.params:
            kind = action.params[name].get('type')
            args[name] = int(value) if (kind == 'integer' or 'integer' in (kind or [])) and value.lstrip('-').isdigit() else value
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


# ---------------------------------------------------------------------------
# What the agent reads as JSON instead of page text (the first message, list_events / search_events)
# ---------------------------------------------------------------------------

def matching_events(event_ids, query):
    """F3: events matching a search by their own name, code, description, address, or one of their locations or
    schedule items (Postgres full text + trigram similarity), best first."""
    from attnd.search import ids
    from events.models import Event, Location, ScheduledItem
    own = ids(Event.objects.filter(pk__in=event_ids), query,
              {'name': 'A', 'event_code': 'A', 'address': 'B', 'description': 'C'})
    places = ids(Location.objects.filter(event_id__in=event_ids), query,
                 {'name': 'A', 'category': 'B', 'snippet': 'C', 'description': 'D'})
    items = ids(ScheduledItem.objects.filter(event_id__in=event_ids), query, {'name': 'A', 'description': 'D'})
    event_of = dict(Location.objects.filter(pk__in=places).values_list('pk', 'event_id'))
    event_of_item = dict(ScheduledItem.objects.filter(pk__in=items).values_list('pk', 'event_id'))
    return list(dict.fromkeys(own + [event_of[p] for p in places] + [event_of_item[i] for i in items]))


def events_json(user, query=''):
    """The person's events as compact event_tile JSON lines (one query for all of them)."""
    found = C.instances(user, 'event_tile', [{}])
    query = (query or '').strip()
    if query:
        order = {pk: i for i, pk in enumerate(matching_events([ids['event_id'] for ids, _ in found], query))}
        found = sorted((f for f in found if f[0]['event_id'] in order), key=lambda f: order[f[0]['event_id']])
    lines = [C.model_json(data, limit=600) for _, data in found]
    if not lines:
        return f'No event matches “{query}”.' if query else 'No events yet.'
    return '\n'.join(lines)


def page_json(user, path):
    """(title, JSON text) of the component a console page is drawn from, or None when it has none."""
    from agent import canvas
    hit = canvas.match(path)
    if hit is None:
        return None
    name, ids, _ = hit
    try:
        found = C.instances(user, name, [ids], 0, 20)
    except (Http404, C.IdError):
        return None
    if not found:
        return None
    title = found[0][1].get('name') or found[0][1].get('title') or C.get(name).title
    return title, '\n'.join(C.model_json(data, limit=3000) for _, data in found)
