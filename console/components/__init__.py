"""Console components: each piece of the organizer console is described once and drawn once.

A component has three parts:

1. **Data function** ``(user, **ids_or_objects) -> dict``: loads the piece for the signed-in person, checking
   permissions exactly like the console views (``Http404`` when they may not see it), and returns plain JSON-able
   data. Pages pass objects their view already loaded for this person (``event=…``, ``form=…``); the Ask Attnd
   chat passes ids (``event_id=3``), which the function loads and checks itself.
2. **Template** (``console/templates/components/<name>.html``) that renders only from that JSON (``d``). It adapts to
   its width with container queries (``.component`` in console.css), so the same template works full-width on a
   page and compact in the ~26rem chat or the phone sheet. ``surface`` is ``'page'`` or ``'chat'`` (page-only
   chrome such as a drag handle, and buttons that in the chat go through Ask Attnd's confirm gate); ``uid``
   prefixes element ids in the chat so they never collide with the page's. Pages may place named template
   partials (``{% component "event_stats" event=event part="numbers" %}``) where their layout needs them.
3. **Actions**: the buttons it offers, each one a registered Ask Attnd action (``agent/registry.py``), so a tap in
   the chat, a typed request and the page run the same view. The data function lists the actions it offers right
   now (``[{'name': 'delete_location', 'label': 'Delete'}]``); ``resolve_actions`` adds the URL, the fixed POST
   values and the ``confirm`` flag from the agent registry (confirms stay server-enforced).

``detail`` names the component a tap in the chat shows (``event_tile`` → ``event_overview``). Pages render with
``{% component "event_tile" event=e %}`` (console_tags); the chat through ``agent/components.py``.
"""

import json
import secrets
from dataclasses import dataclass

from django.http import Http404
from django.template.loader import render_to_string
from django.urls import reverse

REGISTRY = {}
ID_NAMES = ('event_id', 'location_id', 'item_id', 'notification_id', 'changeset_id', 'prospect_id', 'prospect_run_id',
            'proposal_id')
ENTITY = {'event_id': 'event', 'location_id': 'location', 'item_id': 'item', 'notification_id': 'notification',
          'changeset_id': 'changeset', 'prospect_id': 'prospect', 'prospect_run_id': 'prospect_run',
          'proposal_id': 'proposal'}


@dataclass
class Component:
    name: str
    title: str
    description: str
    data: object  # the data function
    ids: tuple = ('event_id',)  # required ids
    optional_ids: tuple = ()  # e.g. location_id on a form (none = a new location)
    actions: tuple = ()  # (agent action name, label): everything it may offer
    detail: str = ''  # what a tap in the chat shows
    parts: tuple = ()  # a composite of these components
    expand: object = None  # (user, **parent ids) -> [ids, …]: a list component given only its parent
    page: object = None  # (ids) -> the page's URL ("Open page ↗")
    form: object = None  # (user, ids, data) -> bound form, for chat submissions that wait for a confirmation
    template: str = ''
    chat: bool = True  # may Ask Attnd show it
    chat_only: str = ''  # why no page draws it (e.g. a canvas cut-down of a page's component); else a page must
    kwarg_map: dict = None  # URL kwarg -> id name, for views whose URLs say pk (Prospects: {'pk': 'prospect_id'})
    audience: str = ''  # 'staff': only staff and superusers (Prospects); absent for everyone else
    act_args: object = None  # (action name, POST) -> extra tool arguments for always-ask buttons with a form (review)
    ai_subject: str = ''  # Add with AI: "the location", "the notification"… (default: from the title)

    def __post_init__(self):
        self.template = self.template or f'components/{self.name}.html'

    @property
    def all_ids(self):
        return tuple(self.ids) + tuple(self.optional_ids)


def component(name, title, description, **options):
    """Register a data function as a component."""
    def decorator(fn):
        if name in REGISTRY:
            raise ValueError(f'component {name} registered twice')
        REGISTRY[name] = Component(name=name, title=title, description=description, data=fn, **options)
        return fn
    return decorator


def registry():
    from console.components import catalog, catalog_chat, catalog_more, catalog_prospects  # noqa: F401  (registers them)
    from agent import proposals  # noqa: F401  (change_preview, stage I)
    return REGISTRY


def get(name):
    comp = registry().get(name)
    if comp is None:
        raise KeyError(name)
    return comp


# ---------------------------------------------------------------------------
# Ids
# ---------------------------------------------------------------------------

class IdError(ValueError):
    pass


def clean_ids(comp, ids):
    """Validate the ids for a component: only its own, integers, the required ones present."""
    ids = {k: v for k, v in (ids or {}).items() if v not in (None, '')}
    unknown = set(ids) - set(comp.all_ids)
    if unknown:
        raise IdError(f'{comp.name} takes {", ".join(comp.all_ids)}, not {", ".join(sorted(unknown))}.')
    out = {}
    for key, value in ids.items():
        try:
            out[key] = int(value)
        except (TypeError, ValueError):
            raise IdError(f'{key} must be a number.') from None
        if out[key] <= 0:
            raise IdError(f'{key} must be a positive number.')
    missing = [k for k in comp.ids if k not in out]
    if missing:
        raise IdError(f'{comp.name} needs {", ".join(missing)}.')
    return out


def instances(user, name, items, start=0, limit=None):
    """[(ids, data)] for one component and a list of id sets, expanding list components given only a parent
    (e.g. location_row with just event_id = every location). Raises IdError / Http404. `start` / `limit` page through
    the expanded list (the canvas shows more with "Show more"; the model's show takes 12)."""
    return instances_page(user, name, items, start, limit)[0]


def instances_page(user, name, items, start=0, limit=None):
    """(found, total): like instances, plus how many there are in all (for "Show more")."""
    comp = get(name)
    if not allowed(comp, user):
        raise Http404
    planned = []  # (ids, preloaded objects)
    for raw in items or [{}]:
        raw = {k: v for k, v in (raw or {}).items() if v not in (None, '')}
        unknown = set(raw) - set(comp.all_ids)
        if unknown:
            raise IdError(f'{comp.name} takes {", ".join(comp.all_ids)}, not {", ".join(sorted(unknown))}.')
        if comp.expand and not all(k in raw for k in comp.ids):
            parent = {k: v for k, v in raw.items() if k in comp.all_ids}
            # An expand loads the list once (the event, annotated rows, one likes count) and hands the objects on,
            # like the pages do: no query per row.
            for entry in comp.expand(user, **clean_parent(parent)):
                planned.append(({k: v for k, v in entry.items() if k in ID_NAMES},
                                {k: v for k, v in entry.items() if k not in ID_NAMES}))
            continue
        planned.append((clean_ids(comp, raw), {}))
    total = len(planned)
    end = None if limit is None else start + limit
    return [(ids, load(user, name, ids, objects)) for ids, objects in planned[start:end]], total


def clean_parent(ids):
    try:
        return {k: int(v) for k, v in ids.items()}
    except (TypeError, ValueError):
        raise IdError('ids must be numbers.') from None


def allowed(comp, user):
    if comp.audience == 'staff':
        return bool(getattr(user, 'is_staff', False) or getattr(user, 'is_superuser', False))
    return True


def for_user(user):
    """The components this person may see (staff-only ones are absent for everyone else)."""
    return {n: c for n, c in registry().items() if allowed(c, user)}


def load(user, name, ids, objects=None):
    """Run the data function for this person (Http404 when they may not see it) and resolve its actions.
    `objects` are rows an expand (or a page's view) already loaded and checked for this person."""
    comp = get(name)
    if not allowed(comp, user):
        raise Http404
    data = comp.data(user, **ids, **(objects or {}))
    return finish(comp, ids, data)


def finish(comp, ids, data):
    data = dict(data)
    data['component'] = comp.name
    data['ids'] = {k: ids[k] for k in comp.all_ids if ids.get(k)}
    if 'actions' in data:
        data['actions'] = resolve_actions(comp, data['ids'], data['actions'])
    if 'parts' in data:
        data['parts'] = [finish(get(p['component']), p['ids'], p) for p in data['parts']]
    for key, form in list(data.items()):  # every form: who it is (fill_form finds it) and Add with AI
        if isinstance(form, dict) and 'fields' in form and 'layout' in form:
            data[key] = dict(form, component=comp.name, ids_json=json.dumps(data['ids'], sort_keys=True, separators=(',', ':')),
                             ai=None if form.get('background') or form.get('no_ai') else ai_offer(comp, data))
    return data


def forms_of(data):
    """The form JSONs in a component's data (form_json output), e.g. event_new_form's blank form."""
    return [v for v in data.values() if isinstance(v, dict) and 'fields' in v and 'layout' in v]


def ai_offer(comp, data):
    """Add with AI on a form: the button's label and the prompt the chat starts with (owner, Nov 21, 2026)."""
    editing = any(k != 'event_id' for k in data.get('ids') or {})
    subject = comp.ai_subject or comp.title.lower().removesuffix(' form')
    if editing:
        return {'label': 'Edit with AI', 'prompt': f'What should change in this {subject}?'}
    return {'label': 'Add with AI', 'prompt': f'Tell me about the {subject}: paste a link, a description, anything.'}


def resolve_actions(comp, ids, offered):
    """Add url, fixed POST values and the confirm flag from Ask Attnd's registry to each offered action."""
    from agent import registry as agent_registry
    writes = agent_registry.registry()['writes']
    declared = dict(comp.actions)
    out = []
    for entry in offered:
        entry = dict(entry)
        name = entry['name']
        if name not in declared:
            raise ValueError(f'{comp.name} does not declare the action {name}')
        action = writes[name]
        alias = comp.kwarg_map or {}
        kwargs = {k: ids[k] if k in ids else ids[alias[k]] for k, _ in action.kwargs}
        entry.setdefault('label', declared[name])
        entry['confirm'] = action.confirm
        entry['url'] = reverse(action.url_name, kwargs=kwargs)
        params = entry['params'] if 'params' in entry else {  # this button's own values (e.g. member)
            k: str(v) for k, v in entry.get('fields', {}).items()}
        entry['params'] = params
        entry['params_json'] = json.dumps(params, sort_keys=True) if params else ''
        entry['fields'] = {**{k: str(v) for k, v in action.fixed.items()}, **params}
        out.append(entry)
    return out


def keys(data):
    """'event:3', 'location:12'… for every entity a component shows (the chat refreshes copies when one changes)."""
    return sorted(f'{ENTITY[k]}:{v}' for k, v in (data.get('ids') or {}).items() if k in ENTITY)


def page_url(name, ids):
    comp = get(name)
    return comp.page(ids) if comp.page else ''


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def new_uid():
    return f'c{secrets.token_hex(3)}-'


def render(name, data, request=None, surface='page', part=None, uid=''):
    comp = get(name)
    template = comp.template + (f'#{part}' if part else '')
    return render_to_string(template, {'d': data, 'surface': surface, 'uid': uid, 'request': request},
                            request=request)


# ---------------------------------------------------------------------------
# What the model gets: the component's JSON, trimmed to useful fields (never HTML)
# ---------------------------------------------------------------------------

DROP_KEYS = {'url', 'locations_url', 'pins', 'center', 'ids_json', 'ai', 'open_app', 'qr_url', 'qr_download_url', 'poster_url', 'image_url', 'review_url', 'usage_url', 'action',
             'multipart', 'layout', 'attrs', 'id_attr'}


def model_json(data, limit=4000):
    """A compact copy for the model: no URLs or markup details; actions → their tool names; forms → field values."""
    def slim(value, key=''):
        if isinstance(value, dict):
            if key == 'form':
                return {name: {k: v for k, v in f.items() if k in ('label', 'value', 'errors', 'required', 'choices')
                               and v not in ('', None, [], False)}
                        for name, f in (value.get('fields') or {}).items()} | (
                    {'errors': value['errors']} if value.get('errors') else {})
            return {k: slim(v, k) for k, v in value.items()
                    if k not in DROP_KEYS and not k.endswith(('_url', '_png', '_svg')) and v not in ('', None, [], {})}
        if isinstance(value, list):
            if key == 'actions':
                return [f'{a["name"]} ({a["label"]}){" — asks to confirm" if a.get("confirm") else ""}' for a in value]
            if key == 'badges':
                return [b['label'] for b in value]
            return [slim(v) for v in value]
        return value
    text = json.dumps(slim(data), ensure_ascii=False, separators=(',', ':'), default=str)
    if len(text) > limit:
        text = text[:limit - 20] + '… (trimmed)'
    return text


# ---------------------------------------------------------------------------
# The generic form component: a Django form as JSON, drawn by components/_form.html
# ---------------------------------------------------------------------------

def _json_value(value):
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    return str(value)


def field_json(bf):
    """One bound field: name, id, label, help, errors, required, the widget's kind/type/attrs/value/options,
    exactly what Django's widget would render (so the agent's prefill and maps.js see the same form)."""
    widget = bf.field.widget
    attrs = bf.build_widget_attrs({}, widget)
    if bf.auto_id and 'id' not in widget.attrs:
        attrs.setdefault('id', bf.auto_id)
    ctx = widget.get_context(bf.html_name, bf.value(), attrs)['widget']
    template = ctx.get('template_name', '')
    kind = ('select' if template.endswith('select.html') else 'radio' if template.endswith('radio.html')
            else 'checkbox' if template.endswith('checkbox.html')
            else 'file' if 'file_input' in template else 'textarea' if template.endswith('textarea.html')
            else 'input')
    out = {
        'name': bf.html_name, 'id': ctx['attrs'].get('id', ''), 'label': str(bf.label or ''),
        'help': str(bf.help_text or ''), 'errors': [str(e) for e in bf.errors],
        'required': bool(bf.field.required), 'widget': kind, 'type': ctx.get('type', ''),
        'attrs': {k: _json_value(v) for k, v in ctx['attrs'].items() if k != 'id' and v is not False and v is not None},
        'value': None if kind in ('select', 'radio', 'file') else _json_value(ctx.get('value')),
    }
    if kind == 'checkbox':
        out['value'] = None
        out['checked'] = bool(ctx['attrs'].get('checked'))
        out['attrs'].pop('checked', None)
    if kind in ('select', 'radio'):
        options = []
        for _, group, _ in ctx.get('optgroups', []):
            for o in group:
                options.append({'value': _json_value(o['value']) if o['value'] is not None else '',
                                'label': str(o['label']), 'selected': bool(o['selected']),
                                'id': o['attrs'].get('id', '')})
        out['options'] = options
        out['choices'] = [o['label'] for o in options][:20]
        out['multiple'] = bool(ctx['attrs'].get('multiple'))
    if kind == 'file':
        initial = ctx.get('value')
        out['current'] = ({'url': getattr(initial, 'url', ''), 'name': str(initial)}
                          if ctx.get('is_initial') and initial else None)
        out['clear'] = ({'name': ctx['checkbox_name'], 'id': ctx['checkbox_id'],
                         'label': str(ctx['clear_checkbox_label'])}
                        if ctx.get('is_initial') and not bf.field.required else None)
        out['initial_text'] = str(ctx.get('initial_text', ''))
        out['input_text'] = str(ctx.get('input_text', ''))
    if kind == 'textarea':
        out['value'] = _json_value(ctx.get('value')) or ''
    return out


def form_json(form, layout, action='', submit=(), confirm_action='', locked=()):
    """A form as JSON. `layout` is a list of rows using field names:
        'name'                              one field
        ('split', 'name', 'category')       main + aside
        ('grid', 'start_time', 'end_time')  side by side (grid--2)
        ('choices', 'when')                 inline radio choices
        ('note', 'text', 'icon')            a small note
        ('block', 'pin_map')                page-only markup the template knows (the map picker)
        ('show_if', 'when:later', [rows])   rows shown only while a field has a value (data-show-if)
        ('details', 'Title', [rows], {...}) a folded section
    """
    fields = {bf.name: field_json(bf) for bf in form}
    for f in fields.values():
        f['locked'] = f['name'].split('-')[-1] in locked

    def row(spec):
        if isinstance(spec, str):
            return {'kind': 'field', 'field': fields[spec]}
        kind = spec[0]
        if kind == 'split':
            return {'kind': 'split', 'main': fields[spec[1]], 'aside': fields[spec[2]]}
        if kind == 'grid':
            return {'kind': 'grid', 'fields': [fields[n] for n in spec[1:]]}
        if kind == 'choices':
            return {'kind': 'choices', 'field': fields[spec[1]]}
        if kind == 'note':
            return {'kind': 'note', 'text': spec[1], 'icon': spec[2] if len(spec) > 2 else ''}
        if kind == 'block':
            return {'kind': 'block', 'name': spec[1], **(spec[2] if len(spec) > 2 else {})}
        if kind == 'show_if':
            return {'kind': 'show_if', 'when': spec[1], 'id': spec[2], 'rows': [row(r) for r in spec[3]]}
        if kind == 'details':
            extra = spec[3] if len(spec) > 3 else {}
            return {'kind': 'details', 'title': spec[1], 'rows': [row(r) for r in spec[2]], **extra}
        raise ValueError(spec)

    return {
        'action': action, 'multipart': form.is_multipart(), 'fields': fields, 'layout': [row(r) for r in layout],
        'errors': [str(e) for e in form.non_field_errors()], 'submit': list(submit), 'confirm_action': confirm_action,
    }


__all__ = ['component', 'registry', 'get', 'load', 'instances', 'render', 'model_json', 'form_json', 'keys',
           'page_url', 'IdError', 'Http404', 'new_uid']
