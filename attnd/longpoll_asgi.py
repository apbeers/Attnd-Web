"""Long-polls without holding a thread (batch 7, Track C; owner: "do it").

Under ASGI (gunicorn's own `asgi` worker, Procfile) this wraps Django's ASGI application. Two requests wait for news:

- the preview phones' `GET /api/v2/events/<id>/changes/?since=N` (API-026, up to 25 s),
- Ask Attnd's `GET /console/agent/runs/<id>/?after=N&wait=10` (stage I14, up to 10 s).

Before Django sees one of them, a short check in a worker thread decides whether this caller may wait (the same rules
the views use: a valid API key / attestation token and a live preview phone of that event; the signed-in owner of an
active run with nothing new yet). Then the request waits **on the event loop**: no thread, no database connection.
One poller per process looks at every waiting event and run in two queries every half second and wakes the ones
whose news arrived. Then the request goes through Django as usual (every middleware, the view's own permission
checks), marked with a per-process token so the view answers at once instead of waiting again
(`WAITED_HEADER` / `waited(request)`).

Everything else, and these requests when the check says no or MAX_WAITING are already waiting, goes straight to
Django unchanged (the views then keep their thread-based wait with its small cap, as under WSGI).
"""
import asyncio
import logging
import re
import secrets
import time
from http.cookies import SimpleCookie
from urllib.parse import parse_qs

from asgiref.sync import sync_to_async

logger = logging.getLogger('attnd.longpoll')

WAITED_HEADER = b'x-attnd-waited'
WAITED_TOKEN = secrets.token_hex(16)  # per process: a client can't claim it waited
MAX_WAITING = 500  # per process; each waiter is a socket and a small future, not a thread
POLL_EVERY = 0.5

CHANGES = re.compile(r'^/api/v2/events/(\d+)/changes/$')
AGENT_RUN = re.compile(r'^/console/agent/runs/(\d+)/$')
CHANGES_SECONDS = 25  # under Heroku's 30 s router timeout (events.preview.WAIT_SECONDS)
AGENT_SECONDS = 10


def waited(request):
    """True when this process's ASGI layer already waited for this request (the view answers at once)."""
    return request.headers.get('X-Attnd-Waited') == WAITED_TOKEN


# ---------------------------------------------------------------------------
# The checks (sync, in a worker thread; every database connection is closed again before returning)
# ---------------------------------------------------------------------------

def _closing(fn):
    def run(*args, **kwargs):
        from django.db import connections
        try:
            return fn(*args, **kwargs)
        finally:
            connections.close_all()  # back to the pool: nothing is held while the request waits
    return run


def _bearer_ok(bearer, event_id):
    from events.apis import ci_key_ok, demo_event, static_key_ok
    from attestation import tokens
    if not bearer:
        return False
    if static_key_ok(bearer) or tokens.verify(bearer) is not None:
        return True
    if ci_key_ok(bearer):
        demo = demo_event()
        return demo is not None and demo.id == event_id
    return False


@_closing
def check_changes(event_id, since, bearer, device_id):
    """The baseline for a preview phone's wait, or None (no wait: Django answers as usual)."""
    from events.models import Device, Event
    if not _bearer_ok(bearer, event_id) or not device_id:
        return None
    version = Event.objects.filter(pk=event_id).values_list('content_version', flat=True).first()
    if version is None or version > since:
        return None
    if not Device.objects.previews().filter(id=device_id[:128], event_id=event_id).exists():
        return None
    return {'since': since}


@_closing
def check_agent_run(run_id, after, session_key):
    """The baseline for Ask Attnd's wait, or None. Only the run's own (signed-in) person may wait on it."""
    from importlib import import_module

    from django.conf import settings
    from django.contrib.auth import BACKEND_SESSION_KEY, HASH_SESSION_KEY, SESSION_KEY, load_backend
    from django.utils.crypto import constant_time_compare

    from agent.models import AgentRun, AgentStep
    if not session_key:
        return None
    session = import_module(settings.SESSION_ENGINE).SessionStore(session_key)
    try:
        user_id = session.get(SESSION_KEY)
        backend_path = session.get(BACKEND_SESSION_KEY)
    except Exception:  # a broken or expired session: Django will answer it
        return None
    if not user_id or backend_path not in settings.AUTHENTICATION_BACKENDS:
        return None
    user = load_backend(backend_path).get_user(user_id)
    if user is None or not constant_time_compare(session.get(HASH_SESSION_KEY, ''), user.get_session_auth_hash()):
        return None
    run = AgentRun.objects.filter(pk=run_id, user=user).values('status', 'step_count').first()
    if run is None or run['status'] not in AgentRun.ACTIVE:
        return None
    if AgentStep.objects.filter(run_id=run_id, seq__gt=after).exists():
        return None
    return {'state': (run['status'], run['step_count']), 'after': after}


@_closing
def snapshot(event_ids, run_ids):
    """Two queries for every waiter of the process: event versions and runs' (status, step count, last step)."""
    from django.db.models import Max

    from agent.models import AgentRun
    from events.models import Event
    events = dict(Event.objects.filter(pk__in=event_ids).values_list('pk', 'content_version')) if event_ids else {}
    runs = {}
    if run_ids:
        for row in AgentRun.objects.filter(pk__in=run_ids).annotate(last=Max('steps__seq')).values(
                'pk', 'status', 'step_count', 'last'):
            runs[row['pk']] = row
    return events, runs


# ---------------------------------------------------------------------------
# Waiting on the event loop
# ---------------------------------------------------------------------------

class Waiters:
    """Every request of this process that waits, and the one poller that wakes them."""

    def __init__(self):
        self.items = {}  # future -> (kind, id, baseline)
        self.task = None

    def __len__(self):
        return len(self.items)

    async def wait(self, kind, obj_id, baseline, seconds):
        future = asyncio.get_running_loop().create_future()
        self.items[future] = (kind, obj_id, baseline)
        if self.task is None or self.task.done():
            self.task = asyncio.ensure_future(self._poll())
        try:
            return await asyncio.wait_for(future, seconds)
        except TimeoutError:
            return False
        finally:
            self.items.pop(future, None)

    async def _poll(self):
        while self.items:
            await asyncio.sleep(POLL_EVERY)
            items = list(self.items.items())
            if not items:
                break
            event_ids = sorted({i for _, (k, i, _) in items if k == 'changes'})
            run_ids = sorted({i for _, (k, i, _) in items if k == 'agent'})
            try:
                events, runs = await sync_to_async(snapshot, thread_sensitive=False)(event_ids, run_ids)
            except Exception:
                logger.exception('long-poll snapshot failed')
                continue
            for future, (kind, obj_id, base) in items:
                if future.done():
                    continue
                if kind == 'changes':
                    version = events.get(obj_id)
                    news = version is None or version > base['since']
                else:
                    row = runs.get(obj_id)
                    news = (row is None or (row['status'], row['step_count']) != base['state']
                            or (row['last'] or 0) > base['after'])
                if news:
                    future.set_result(True)


def _header(scope, name):
    for key, value in scope.get('headers') or ():
        if key == name:
            return value.decode('latin-1')
    return ''


def _int(query, name):
    raw = (query.get(name) or [''])[0]
    return int(raw) if raw.isdigit() else None


class LongPollMiddleware:
    """ASGI middleware around Django's application (attnd/asgi.py)."""

    def __init__(self, app):
        self.app = app
        self.waiters = Waiters()

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http' or scope.get('method') != 'GET':
            return await self.app(scope, receive, send)
        # Nobody but this layer may set the header.
        headers = [(k, v) for k, v in scope.get('headers') or () if k != WAITED_HEADER]
        scope = {**scope, 'headers': headers}
        plan = await self._plan(scope)
        if plan is None or len(self.waiters) >= MAX_WAITING:
            return await self.app(scope, receive, send)
        kind, obj_id, baseline, seconds = plan

        disconnected = asyncio.Event()
        buffered = []

        async def watch():
            while True:
                message = await receive()
                if message['type'] == 'http.disconnect':
                    disconnected.set()
                    return
                buffered.append(message)

        watcher = asyncio.ensure_future(watch())
        waiter = asyncio.ensure_future(self.waiters.wait(kind, obj_id, baseline, seconds))
        gone = asyncio.ensure_future(disconnected.wait())
        started = time.monotonic()
        await asyncio.wait({waiter, gone}, return_when=asyncio.FIRST_COMPLETED)
        for task in (waiter, gone):
            if not task.done():
                task.cancel()
        if disconnected.is_set():
            watcher.cancel()
            return  # the phone or browser went away: nothing to answer
        logger.debug('long-poll %s %s waited %.1fs', kind, obj_id, time.monotonic() - started)

        # The watcher keeps the server's receive() for itself until Django is done (cancelling it while it waits for
        # the disconnect marks gunicorn's request closed, and Django would then abort the answer).
        async def replay():
            if buffered:
                return buffered.pop(0)
            await disconnected.wait()
            return {'type': 'http.disconnect'}

        scope = {**scope, 'headers': [*headers, (WAITED_HEADER, WAITED_TOKEN.encode())]}
        try:
            return await self.app(scope, replay, send)
        finally:
            watcher.cancel()

    async def _plan(self, scope):
        path, query = scope.get('path', ''), parse_qs(scope.get('query_string', b'').decode('latin-1'))
        if match := CHANGES.match(path):
            since = _int(query, 'since')
            if since is None:
                return None
            event_id = int(match.group(1))
            bearer = _header(scope, b'authorization').removeprefix('Bearer ').strip()
            base = await sync_to_async(check_changes, thread_sensitive=False)(
                event_id, since, bearer, _header(scope, b'x-device-id'))
            return ('changes', event_id, base, CHANGES_SECONDS) if base else None
        if match := AGENT_RUN.match(path):
            wait, after = _int(query, 'wait'), _int(query, 'after')
            if not wait:
                return None
            from django.conf import settings
            cookie = SimpleCookie()
            try:
                cookie.load(_header(scope, b'cookie'))
            except Exception:
                return None
            morsel = cookie.get(settings.SESSION_COOKIE_NAME)
            run_id = int(match.group(1))
            base = await sync_to_async(check_agent_run, thread_sensitive=False)(
                run_id, after or 0, morsel.value if morsel else '')
            return ('agent', run_id, base, min(wait, AGENT_SECONDS)) if base else None
        return None
