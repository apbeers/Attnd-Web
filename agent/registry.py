"""Ask Attnd's action registry: which console views the agent may run, and how.

Every console view is either registered here (with ``@agent_action`` on the view itself) or listed in
``EXCLUDED`` with a reason; ``agent/tests.py`` fails CI otherwise, so new console pages and buttons reach the agent
(or are refused on purpose) the day they are built.

    @agent_action('edit_location', 'Edit a location of an event.', form=LocationForm,
                  url_name='console:location_edit', prefill=True)
    @agent_action('location_page', 'A location\'s edit page.', kind='read', url_name='console:location_edit')
    @event_view
    def location_edit(request, event, location_id=None): ...

* ``kind='read'``: a GET page the agent may open with ``read_page`` (listed by ``list_pages``). Reads are not tools
  of their own.
* ``kind='write'``: a tool. Its parameters are the URL's kwargs (``event_id``, ``location_id``…) plus the fields of
  ``form`` (derived from the Django form: names, types, choices, required, help text; null = keep the current value
  / leave empty) plus ``params`` (hand-written JSON schemas for views that read ``request.POST`` directly).
  The agent runs it as a real POST to the view (agent/internal.py), so permissions, validation, History, version
  bumps and pushes are exactly the person's.
* ``fixed``: POST data always sent (e.g. ``{'action': 'delete'}``); ``build(args, ctx)``: an adapter returning more
  POST data (formsets, ``accept_<id>`` style names); ``prefill``: GET the page first and start from the form's
  current values like a browser does (True = the same URL, or a URL name with the same kwargs); ``json``: send
  ``Accept: application/json`` (the inline-JSON endpoints).
* ``confirm=True``: always ask. The run pauses and the person sees ``summary(ctx)`` with Confirm / Cancel; nothing
  runs until they click. Required for every button with ``data-confirm`` and for anything that reaches people
  outside the team or can't be undone (tests check the ``data-confirm`` ones).
* ``audience``: who sees the tool / page: ``all`` (default for console and Studio-plan pages, the views' own checks
  still apply), ``studio`` (Studio access), ``staff`` (diagnostics, prospects, Studio reports), ``superuser``.
"""

import inspect
import re
from dataclasses import dataclass, field
from functools import lru_cache

from django import forms
from django.conf import settings
from django.urls import URLPattern, URLResolver, get_resolver

# Never registered, refused by read_page, whatever the request: account security, sign-out, the admin.
OFF_LIMITS_PREFIXES = ('/accounts/', '/console/account/', '/console/signup/', '/console/agent/', '/console/assistant/')


def off_limits(path):
    admin = '/' + settings.ADMIN_URL_PATH
    return path.startswith(OFF_LIMITS_PREFIXES) or path.startswith(admin) or path.startswith('/admin/') \
        or '/logout' in path


# Console URL names the agent never uses, with the reason (the coverage test reads this).
EXCLUDED = {
    'console:signup': 'Off-limits: creating accounts (and the agent only runs for signed-in people).',
    'console:account': 'Off-limits: account security (email, password, passkeys, two-factor, recovery codes).',
    'console:service_worker': 'A static script for the browser, no content.',
    'console:walkthrough_skip': 'The chat\'s own "Skip" on the first-run walkthrough (a person\'s choice, no content).',
    'console:manifest': 'The web app manifest, no content.',
    'console:stripe_webhook': "Stripe's own server calls it (signed); never a person or the agent.",
    'console:event_qr': 'A PNG image of the join QR code (the overview has the join link).',
    'console:event_poster': 'A printable poster; the overview has the same join link and code.',
    'studio:version_preview': 'Redirects to a signed preview link on the sites host (outside the console).',
    'platform:customers_csv': 'CSV download of the customers list; the Customers tab shows the same.',
    'credit:export': 'CSV download of the month\'s AI calls; the AI spend page shows the same numbers.',
    'console:event_preview_qr': 'An image (the preview QR code), no content.',
    'console:event_phones': 'JSON for the QR card\'s live phone line; the card shows the same.',
    'prospects:export': 'CSV download of organizer contacts; the list and detail pages show the same data.',
    'diagnostics:session_export': 'A .jsonl file download; the session page and entries.json have the entries.',
    'console:place_search': 'Address search for the location form\'s Find button (JSON); the agent fills addresses itself.',
    'console:location_fill': 'Suggestions for the location form from a website (JSON, saves nothing; the person reviews).',
    'console:export': 'CSV downloads (schedule, locations, notifications, metrics); the pages show the same data.',
    'console:event_theme_css': 'JSON the page reads to swap its colours in place; event_theme_page shows the same theme.',
    'console:assistant': 'Ask Attnd\'s own full-screen chat page: the agent never drives itself.',
    'agent:start': 'Ask Attnd\'s own endpoints: the agent never drives itself.',
    'agent:runs': 'Ask Attnd\'s own endpoints.',
    'agent:run': 'Ask Attnd\'s own endpoints.',
    'agent:confirm': 'Ask Attnd\'s own endpoints (only the person\'s click confirms).',
    'agent:cancel': 'Ask Attnd\'s own endpoints.',
    'agent:reply': 'Ask Attnd\'s own endpoints.',
    'agent:stop': 'Ask Attnd\'s own endpoints.',
    'agent:undo': 'Ask Attnd\'s own endpoints.',
    'agent:feedback': 'Ask Attnd\'s own endpoints (the person\'s 👍 / 👎).',
    'agent:contact': 'Ask Attnd\'s own endpoints (the person writes to the Attnd team themselves).',
    'agent:proposal_apply': 'Ask Attnd\'s own endpoints (only the person\'s Apply on a change preview).',
    'agent:attach': 'Ask Attnd\'s own endpoints.',
    'agent:component_render': 'Ask Attnd\'s own endpoints (the chat\'s components).',
    'agent:component_batch': 'Ask Attnd\'s own endpoints (several components in one request).',
    'agent:component_pick': 'Ask Attnd\'s own endpoints (the chat\'s components).',
    'agent:component_act': 'Ask Attnd\'s own endpoints (only the person\'s click confirms).',
}

# Console views whose source handles POST but that have no write action, with the reason.
POST_EXCLUDED = {
    'console:signup': EXCLUDED['console:signup'],
    'console:account': EXCLUDED['console:account'],
    'console:location_fill': EXCLUDED['console:location_fill'],
    'console:walkthrough_skip': EXCLUDED['console:walkthrough_skip'],
    'console:stripe_webhook': EXCLUDED['console:stripe_webhook'],
}

NAMESPACE_AUDIENCE = {'diagnostics': 'staff', 'prospects': 'staff', 'studio': 'studio', 'credit': 'staff',
                      'platform': 'superuser'}
URL_AUDIENCE = {'studio:reports': 'staff', 'console:admin_usage': 'superuser', 'console:event_paid': 'staff'}
# Event pages that also need a platform permission (the view checks it too).
URL_PERMISSION = {'console:admin_usage': 'events.view_all_usage', 'console:event_paid': 'events.mark_event_paid'}

TOOL_NAME_RE = re.compile(r'^[a-z][a-z0-9_]{2,63}$')
MAX_ENUM = 40


@dataclass
class Action:
    name: str
    description: str
    kind: str = 'write'
    confirm: bool = False
    summary: object = None  # callable(ctx) -> str, the confirm card's text
    form: object = None  # a Django form class
    prefix: str = ''
    fixed: dict = field(default_factory=dict)
    params: dict = field(default_factory=dict)  # name -> JSON schema (hand-written parameters)
    build: object = None  # callable(args, ctx) -> dict of POST data
    file_params: dict = field(default_factory=dict)  # param (attachment ids) -> POST file field name
    url_name: str = ''
    prefill: object = False
    json: bool = False
    returns_page: bool = False  # a 200 page (not a redirect) is the answer (e.g. a retrace)
    audience: str = ''  # a label for tests and areas; who may see it is `permission`
    permission: str = ''  # the permission it needs (default: what its view declares, attnd.permissions)
    exclude_fields: tuple = ()
    title: str = ''  # what the step list says ("Edited a location")
    # Filled in by the registry:
    view: object = None
    route: str = ''
    kwargs: tuple = ()  # URL kwargs in order, with their converter type: (('event_id', 'int'), ...)

    @property
    def is_write(self):
        return self.kind == 'write'

    def label(self):
        return self.title or self.name.replace('_', ' ').capitalize()


def agent_action(name, description, kind='write', confirm=False, summary=None, **options):
    """Register a console view with Ask Attnd. Stack it (outermost) once per tool / readable page."""
    if kind not in ('read', 'write'):
        raise ValueError('kind must be read or write')
    if kind == 'read' and confirm:
        raise ValueError('reads never need a confirmation')
    spec = Action(name=name, description=description, kind=kind, confirm=confirm, summary=summary, **options)

    def decorator(view):
        actions = view.__dict__.setdefault('_agent_actions', [])
        actions.insert(0, spec)  # decorators apply bottom-up; keep source order
        return view
    return decorator


# ---------------------------------------------------------------------------
# Building the registry from the URL conf
# ---------------------------------------------------------------------------

def walk(patterns=None, prefix='', namespace=None):
    """(route, 'ns:name', URLPattern) for every URL pattern, with its full route."""
    if patterns is None:
        patterns = get_resolver().url_patterns
    for p in patterns:
        if isinstance(p, URLResolver):
            ns = p.namespace or namespace
            yield from walk(p.url_patterns, prefix + str(p.pattern), ns)
        elif isinstance(p, URLPattern):
            name = f'{namespace}:{p.name}' if namespace and p.name else (p.name or '')
            yield prefix + str(p.pattern), name, p


def console_patterns():
    return [(route, name, p) for route, name, p in walk() if route.startswith('console/')]


CONVERTER_RE = re.compile(r'<(?:(?P<conv>[a-z]+):)?(?P<name>\w+)>')


def _kwargs(route):
    return tuple((m.group('name'), m.group('conv') or 'str') for m in CONVERTER_RE.finditer(route))


def _audience(spec, url_name):
    if spec.audience:
        return spec.audience
    if url_name in URL_AUDIENCE:
        return URL_AUDIENCE[url_name]
    return NAMESPACE_AUDIENCE.get(url_name.split(':')[0], 'all')


class RegistryError(Exception):
    pass


@lru_cache(maxsize=1)
def registry():
    """{'writes': {tool name: Action}, 'reads': {url name: Action}} built from the URL conf."""
    writes, reads = {}, {}
    by_view = {}
    for route, name, pattern in console_patterns():
        by_view.setdefault(id(pattern.callback), []).append((route, name, pattern))
    for route, name, pattern in console_patterns():
        for spec in getattr(pattern.callback, '_agent_actions', []):
            routes = by_view[id(pattern.callback)]
            if spec.url_name:
                if spec.url_name != name:
                    if spec.url_name not in {n for _, n, _ in routes}:
                        raise RegistryError(f'{spec.name}: {spec.url_name} is not a URL of this view')
                    continue
            elif len(routes) > 1:
                raise RegistryError(f'{spec.name}: this view has several URLs; pass url_name')
            if off_limits('/' + route):
                raise RegistryError(f'{spec.name}: /{route} is off-limits to the agent')
            action = Action(**{**spec.__dict__})
            action.url_name, action.route, action.view = name, route, pattern.callback
            action.kwargs = _kwargs(route)
            action.audience = _audience(spec, name)
            from attnd.permissions import declared
            action.permission = spec.permission or URL_PERMISSION.get(name) or declared(pattern.callback) or ''
            if action.kind == 'read':
                reads.setdefault(name, action)
            else:
                if not TOOL_NAME_RE.match(action.name):
                    raise RegistryError(f'{action.name}: tool names are lower_snake_case')
                if action.name in writes or action.name in RESERVED_TOOLS:
                    raise RegistryError(f'{action.name}: registered twice')
                writes[action.name] = action
    return {'writes': writes, 'reads': reads}


RESERVED_TOOLS = {'search_everything', 'remember', 'propose_changes', 'read_page', 'list_pages', 'list_events', 'search_events', 'ask_user', 'finish', 'show',
                  'get_component'}


def allowed(action, user):
    """May this user see this tool or page at all? (The view still decides what they may do.)"""
    from attnd.permissions import PUBLIC, is_platform
    if not user or not user.is_authenticated:
        return False
    perm = action.permission
    if not perm or perm == PUBLIC:
        return False  # deny by default (a view without a declared permission is refused anyway)
    if is_platform(perm):
        return user.has_perm(perm)
    return True  # signed-in or per-event: the view checks the event (attnd.permissions)


def writes_for(user):
    return {name: a for name, a in registry()['writes'].items() if allowed(a, user)}


def reads_for(user):
    return {name: a for name, a in registry()['reads'].items() if allowed(a, user)}


def handles_post(view):
    """True when a view's source reads POST data (used by the coverage test)."""
    try:
        source = inspect.getsource(inspect.unwrap(view))
    except (OSError, TypeError):
        return False
    return 'POST' in source or 'require_POST' in source


# ---------------------------------------------------------------------------
# Tool schemas
# ---------------------------------------------------------------------------

def _nullable(schema):
    schema = dict(schema)
    kind = schema.get('type')
    schema['type'] = [kind, 'null'] if isinstance(kind, str) else [*kind, 'null']
    if 'enum' in schema:
        schema['enum'] = [*schema['enum'], None]
    return schema


def _text(*parts, limit=130):  # (tool schemas go into every model call: short)
    text = ' '.join(str(p).strip() for p in parts if p and str(p).strip())
    text = re.sub(r'\s+', ' ', text)
    return text if len(text) <= limit else text[:limit - 1] + '…'


def field_schema(name, f):
    """JSON schema for one Django form field (without null; the caller adds it)."""
    label = str(f.label or name.replace('_', ' ').capitalize())
    desc = _text(f'{label}: {f.help_text}' if f.help_text else label)
    if isinstance(f, (forms.FileField,)):
        return {'type': 'string', 'description': _text(desc, '(the id of a file the person attached, see ATTACHMENTS)')}
    if isinstance(f, forms.BooleanField):
        return {'type': 'boolean', 'description': desc}
    if isinstance(f, forms.ModelMultipleChoiceField):
        return {'type': 'array', 'items': {'type': 'integer'},
                'description': _text(desc, f'(ids of {f.queryset.model._meta.verbose_name_plural})')}
    if isinstance(f, forms.ModelChoiceField):
        none = '; 0 = none' if not f.required else ''
        return {'type': 'integer', 'description': _text(desc, f'(id of a {f.queryset.model._meta.verbose_name}{none})')}
    if isinstance(f, forms.MultipleChoiceField):
        values = [str(v) for v, _ in f.choices if str(v) != '']
        return {'type': 'array', 'items': {'type': 'string', 'enum': values}, 'description': desc}
    if isinstance(f, forms.ChoiceField):
        values = [str(v) for v, _ in f.choices]
        if len(values) <= MAX_ENUM:
            labels = '; '.join(f'{v or "(empty)"} = {lbl}' for v, lbl in f.choices if str(lbl) != str(v))
            return {'type': 'string', 'enum': values, 'description': _text(desc, labels, limit=220)}
        example = values[min(len(values) - 1, 3)]
        return {'type': 'string', 'description': _text(desc, f'(one of {len(values)} values, e.g. {example})')}
    if isinstance(f, forms.DateTimeField):
        return {'type': 'string', 'description': _text(desc, "(the event's local time, YYYY-MM-DDTHH:MM)")}
    if isinstance(f, forms.DateField):
        return {'type': 'string', 'description': _text(desc, '(YYYY-MM-DD)')}
    if isinstance(f, (forms.DecimalField, forms.FloatField)):  # (before IntegerField: they subclass it)
        return {'type': 'number', 'description': desc}
    if isinstance(f, forms.IntegerField):
        return {'type': 'integer', 'description': desc}
    return {'type': 'string', 'description': _text(desc, '(max %d characters)' % f.max_length
                                                    if getattr(f, 'max_length', None) else '')}


def form_fields(form_class):
    """The form's fields, from an unbound instance (forms add fields in __init__), else its base fields."""
    try:
        return dict(form_class().fields)
    except Exception:
        return dict(form_class.base_fields)


def form_field_names(action):
    if not action.form:
        return []
    return [n for n in form_fields(action.form) if n not in action.exclude_fields and n not in action.fixed]


def tool_schema(action):
    """A strict function schema: every property required, optional ones nullable (null = keep / leave empty)."""
    props = {}
    for name, conv in action.kwargs:
        props[name] = {'type': 'integer' if conv == 'int' else 'string',
                       'description': f'{name.replace("_", " ")} (from the page URL or list_pages)'}
    if action.form:
        fields_ = form_fields(action.form)
        for name in form_field_names(action):
            f = fields_[name]
            schema = field_schema(name, f)
            if f.required and not action.prefill:
                schema['description'] = _text(schema.get('description'), '(required)', limit=420)
            props[name] = _nullable(schema)
    for name, schema in action.params.items():
        props[name] = schema
    return {'type': 'object', 'additionalProperties': False, 'required': list(props), 'properties': props}


def tool_definition(action):
    description = action.description
    if action.confirm:
        description += ' Always asks the person to confirm first.'
    return {'name': action.name, 'description': description, 'parameters': tool_schema(action), 'strict': True}


def param(kind, description, enum=None, nullable=False, items=None):
    """A hand-written parameter schema for `params`."""
    schema = {'type': kind, 'description': description}
    if enum is not None:
        schema['enum'] = list(enum)
    if items is not None:
        schema['items'] = items
    return _nullable(schema) if nullable else schema
