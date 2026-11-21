"""Running console views as the signed-in person (Ask Attnd's tools).

Each tool call becomes an internal request that goes through the whole middleware stack and the real view, exactly
like the person's browser request: their session (read-only, see agent.middleware), the two-factor middleware, the
view's permission checks, form validation, messages, History, ``content_version`` bumps, pushes and signals. CSRF
is waived because the request never left the server and is built for an authenticated session. Nothing here
duplicates business logic: a write is "fill in the form the page shows and press its button".
"""

import json
import logging
from urllib.parse import urlsplit

from django.contrib.messages import get_messages
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.handlers.base import BaseHandler
from django.test.client import MULTIPART_CONTENT, RequestFactory
from django.urls import Resolver404, resolve, reverse
from django.utils.datastructures import MultiValueDict

from agent import context, pages, registry

logger = logging.getLogger('attnd.agent')

MAX_RESULT_CHARS = 12_000

_handler = None


def handler():
    global _handler
    if _handler is None:
        h = BaseHandler()
        h.load_middleware()
        _handler = h
    return _handler


class Signedout(Exception):
    """The person's session ended (signed out, expired) or needs the second sign-in step again."""


class Response:
    def __init__(self, raw, path):
        self.raw = raw
        self.path = path
        self.status = raw.status_code
        self.location = raw.get('Location', '') if 300 <= raw.status_code < 400 else ''
        self.content_type = raw.get('Content-Type', '')
        self.messages = []
        undo = raw.get('X-Attnd-Undo', '')  # console/undo.py recorded this change
        self.undo = int(undo) if undo.isdigit() else None

    @property
    def html(self):
        return 'text/html' in self.content_type

    @property
    def is_json(self):
        return 'json' in self.content_type

    def text(self):
        if getattr(self.raw, 'streaming', False):
            return b''.join(self.raw.streaming_content).decode('utf-8', 'replace')
        return self.raw.content.decode(self.raw.charset or 'utf-8', 'replace')

    def json(self):
        try:
            return json.loads(self.text())
        except ValueError:
            return None


def _session(run):
    from importlib import import_module

    from django.conf import settings
    engine = import_module(settings.SESSION_ENGINE)
    return engine.SessionStore(session_key=run.session_key or None)


def request(run, method, path, data=None, files=None, accept_json=False, step=None):
    """One internal request to a console URL as the run's person. Returns a Response."""
    factory = RequestFactory()
    headers = {'HTTP_HOST': run.host or 'localhost', 'HTTP_ACCEPT': 'application/json' if accept_json else 'text/html',
               'HTTP_X_ATTND_AGENT': f'{run.pk}/{step.seq if step else 0}', 'REMOTE_ADDR': '127.0.0.1'}
    if method == 'GET':
        req = factory.get(path, data or {}, secure=run.secure, **headers)
    else:
        payload = MultiValueDict()
        for key, values in (data or {}).items():
            payload.setlist(key, list(values) if isinstance(values, (list, tuple)) else [values])
        body = {k: payload.getlist(k) for k in payload}
        for key, upload in (files or {}).items():
            body[key] = upload
        req = factory.post(path, body, content_type=MULTIPART_CONTENT, secure=run.secure, **headers)
    req._dont_enforce_csrf_checks = True
    req.agent_session = _session(run)
    req.agent_run_id = run.pk
    req.agent_step_id = step.pk if step else None
    with context.acting(run, step):
        raw = handler().get_response(req)
    response = Response(raw, path)
    try:
        response.messages = [(m.level_tag or 'info', str(m)) for m in get_messages(req)]
    except Exception:
        response.messages = []
    logger.info(json.dumps({'agent_run': run.pk, 'agent_step': step.seq if step else 0, 'method': method,
                            'path': path, 'status': response.status}))
    _check_signed_in(response)
    return response


def _check_signed_in(response):
    target = urlsplit(response.location).path if response.location else ''
    if target.startswith(('/accounts/login', '/accounts/2fa')):
        raise Signedout('You are signed out of the console (or it needs your second sign-in step), so Ask Attnd '
                        'stopped. Sign in again and ask once more.')


def trim(text, offset=0, limit=MAX_RESULT_CHARS):
    part = text[offset:offset + limit]
    if offset + limit < len(text):
        part += f'\n… (more: read_page with offset={offset + limit}; {len(text)} characters in all)'
    return part


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------

def resolve_console(path):
    """(url name, kwargs) of a console path, or raise ValueError."""
    path = urlsplit(path).path
    if not path.startswith('/console/') or registry.off_limits(path):
        raise ValueError('That page is not available to Ask Attnd.')
    try:
        match = resolve(path)
    except Resolver404 as exc:
        raise ValueError('No console page has that address.') from exc
    return match.view_name, match.kwargs


def read(run, path, offset=0, step=None):
    """GET a registered console page as the person: (title, text for the model, final path)."""
    parts = urlsplit(path)
    name, _ = resolve_console(parts.path)
    reads = registry.reads_for(run.user)
    if name not in reads:
        raise ValueError('Ask Attnd can\'t open that page (it isn\'t one of the readable console pages).')
    query = dict(pair.split('=', 1) if '=' in pair else (pair, '') for pair in parts.query.split('&') if pair)
    response = request(run, 'GET', parts.path, data=query, step=step)
    hops = 0
    while response.location and hops < 3:
        hops += 1
        target = urlsplit(response.location)
        try:
            resolve_console(target.path)
        except ValueError:
            return 'Redirect', f'The page redirected outside the console ({target.path}).', target.path
        response = request(run, 'GET', target.path, step=step)
    if response.status == 404:
        raise ValueError('Not found, or you don\'t have access to it.')
    if response.status >= 400:
        raise ValueError(f'The page answered HTTP {response.status}.')
    final = response.path
    if response.is_json:
        return reads[name].label(), trim(json.dumps(response.json(), ensure_ascii=False, separators=(',', ':')),
                                         offset), final
    if response.html:
        title, text = pages.page_text(response.text(), final)
        return title or reads[name].label(), trim(text, offset), final
    return reads[name].label(), trim(response.text(), offset), final


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------

class ActionContext:
    """What summary() and build() callables get."""

    def __init__(self, run, action, args):
        self.run, self.action, self.args = run, action, dict(args)
        self.user = run.user
        self._event = None

    @property
    def event(self):
        if self._event is None and self.args.get('event_id'):
            from events.models import Event
            self._event = Event.objects.filter(pk=self.args['event_id']).first()
        return self._event


def action_url(action, args):
    kwargs = {}
    for name, conv in action.kwargs:
        value = args.get(name)
        if value in (None, ''):
            raise ValueError(f'{name} is required.')
        kwargs[name] = int(value) if conv == 'int' else str(value)
    return reverse(action.url_name, kwargs=kwargs)


def _choose_form(found, target, action, names):
    candidates = [f for f in found if f['method'] == 'post' and f['action'] == target]
    if not candidates:
        return None

    def score(f):
        fixed_ok = all(any(n == k and v == str(val) for n, v in f['fields']) or k not in f['names']
                       for k, val in action.fixed.items())
        return (fixed_ok, len(set(names) & f['names']))
    return max(candidates, key=score)


def _post_value(value):
    if isinstance(value, bool):
        return 'on' if value else None
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return str(value)


def build_data(run, action, args, step=None):
    """(url, POST data, files, before) for a write: the page's current form values (prefill), the fixed values,
    then the model's arguments on top. `before` = the prefilled values, for the step's change text."""
    from agent.models import AgentAttachment
    url = action_url(action, args)
    data, before = {}, {}
    prefix = f'{action.prefix}-' if action.prefix else ''
    names = [prefix + n for n in registry.form_field_names(action)]
    checkables = {}
    if action.prefill:
        page = url if action.prefill is True else reverse(action.prefill, kwargs={
            k: args[k] for k, _ in registry._kwargs(reverse_route(action.prefill))})
        response = request(run, 'GET', page, step=step)
        if response.status == 404:
            raise ValueError('Not found, or you don\'t have access to it.')
        if response.status != 200 or not response.html:
            raise ValueError(f'Could not open the page with the form (HTTP {response.status}).')
        form = _choose_form(pages.forms(response.text(), page), url, action, names)
        if form is None:
            raise ValueError('The form for this action isn\'t on the page (you may not have access to it).')
        for name, value in form['fields']:
            data.setdefault(name, []).append(value)
        checkables = form.get('checkables', {})
        before = {k: (v[0] if len(v) == 1 else v) for k, v in data.items()}
    for key, value in action.fixed.items():
        data[key] = [str(value)]
    files = {}
    form_fields = registry.form_fields(action.form) if action.form else {}
    for name in registry.form_field_names(action):
        value = args.get(name)
        if value is None:
            continue
        key = prefix + name
        f = form_fields[name]
        from django import forms as dj_forms
        if isinstance(f, dj_forms.FileField):
            attachment = AgentAttachment.objects.filter(pk=_int(value), user=run.user).first()
            if attachment is None:
                raise ValueError(f'{name}: no attached file with id {value}.')
            files[key] = SimpleUploadedFile(attachment.name, bytes(attachment.data), attachment.content_type)
            continue
        if isinstance(f, dj_forms.ModelChoiceField) and not isinstance(f, dj_forms.ModelMultipleChoiceField) \
                and value in (0, '0'):
            data[key] = ['']  # "none"
        elif isinstance(value, list):
            data[key] = [_post_value(v) for v in value]
        elif isinstance(value, bool) and not isinstance(f, dj_forms.ChoiceField):
            if value:
                values = checkables.get(key) or ['on']
                data[key] = [values[0]]
            else:
                data.pop(key, None)
        else:
            data[key] = [_post_value(value)]
    if action.build:
        extra = action.build(args, ActionContext(run, action, args)) or {}
        if not isinstance(extra, dict):
            extra = {}
        for key, value in extra.items():
            if value is None:
                data.pop(key, None)
            else:
                data[key] = [str(v) for v in value] if isinstance(value, (list, tuple)) else [str(value)]
    for pname, key in action.file_params.items():
        ids = args.get(pname) or []
        uploads = []
        for attachment_id in (ids if isinstance(ids, list) else [ids]):
            attachment = AgentAttachment.objects.filter(pk=_int(attachment_id), user=run.user).first()
            if attachment is None:
                raise ValueError(f'{pname}: no attached file with id {attachment_id}.')
            uploads.append(SimpleUploadedFile(attachment.name, bytes(attachment.data), attachment.content_type))
        if uploads:
            files[key] = uploads
    if not action.build and action.params:  # hand-written parameters go in under their own names
        for name in action.params:
            if name in action.file_params:
                continue
            value = args.get(name)
            if value is None:
                continue
            if isinstance(value, bool):
                if value:
                    data[name] = ['on']
                else:
                    data.pop(name, None)
            elif isinstance(value, list):
                data[name] = [_post_value(v) for v in value]
            else:
                data[name] = [_post_value(value)]
    return url, data, files, before


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def reverse_route(url_name):
    for route, name, _ in registry.console_patterns():
        if name == url_name:
            return route
    return ''


def execute(run, action, args, step=None):
    """Run a write action. Returns a dict: ok, changed, text (one line for the panel), result (for the model), url."""
    url, data, files, before = build_data(run, action, args, step)
    response = request(run, 'POST', url, data=data, files=files, accept_json=action.json, step=step)
    messages = response.messages
    errors_msgs = [text for level, text in messages if level == 'error']
    out = {'ok': True, 'changed': False, 'url': url, 'messages': [text for _, text in messages]}
    if response.status == 404:
        out.update(ok=False, result='Not found, or you don\'t have permission to do that.')
    elif response.status == 403:
        out.update(ok=False, result='Not allowed (HTTP 403).')
    elif response.status == 405:
        out.update(ok=False, result='That page doesn\'t accept this action (HTTP 405).')
    elif response.status >= 500:
        out.update(ok=False, result=f'The server failed (HTTP {response.status}).')
    elif response.is_json:
        payload = response.json()
        if response.status >= 400:
            out.update(ok=False, result=json.dumps({'error': payload}, ensure_ascii=False)[:4000])
        else:
            out.update(changed=True, result=json.dumps(payload, ensure_ascii=False)[:4000])
    elif response.location:
        target = urlsplit(response.location).path
        out['url'] = target
        if errors_msgs:
            out.update(ok=False, result='Not done: ' + ' '.join(errors_msgs))
        else:
            out.update(changed=True, result=json.dumps({'done': True, 'messages': out['messages'],
                                                        'now_on': target}, ensure_ascii=False))
    elif response.status == 200 and response.html:
        html = response.text()
        errors = pages.form_errors(html, url)
        if errors or errors_msgs:
            if errors_msgs:
                errors.setdefault('__all__', []).extend(errors_msgs)
            out.update(ok=False, result=json.dumps({'done': False, 'errors': errors}, ensure_ascii=False))
        elif action.returns_page:
            _, text = pages.page_text(html, url)
            out.update(result=trim(text, 0, 8000))
        else:
            _, text = pages.page_text(html, url)
            out.update(ok=False, result='The page came back without a confirmation. Page now:\n' + trim(text, 0, 3000))
    else:
        out.update(ok=bool(200 <= response.status < 300), changed=200 <= response.status < 300,
                   result=f'HTTP {response.status}')
    out['changes'] = _changes(action, args, before)
    out['undo'] = response.undo
    return out


def _changes(action, args, before):
    """'start time → 2026-11-14T20:00' for the fields the model set, when they differ from the form's values."""
    parts = []
    prefix = f'{action.prefix}-' if action.prefix else ''
    for name in registry.form_field_names(action):
        value = args.get(name)
        if value is None:
            continue
        old = before.get(prefix + name)
        new = value if not isinstance(value, bool) else ('on' if value else '')
        if before and str(old or '') == str(new):
            continue
        shown = str(new) if new != '' else 'off'
        parts.append(f'{name.replace("_", " ")} → {shown[:60]}')
    return parts[:6]
