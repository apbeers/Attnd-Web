"""The web dyno's ASGI stack (attnd/asgi.py): Django on a thread pool (attnd/wsgi_bridge.py) and long-polls that wait
on the event loop (attnd/longpoll_asgi.py)."""
import asyncio
import json
import threading
import time
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TransactionTestCase, override_settings
from django.utils import timezone

from attnd import longpoll_asgi
from events.models import Device, Event


def call(app, path, query='', headers=(), method='GET', body=b'', disconnect_after=None):
    """One request through an ASGI app; returns (status, headers dict, body bytes)."""
    async def go():
        scope = {'type': 'http', 'method': method, 'path': path, 'raw_path': path.encode(),
                 'query_string': query.encode(), 'headers': [(k.lower().encode(), v.encode()) for k, v in headers],
                 'http_version': '1.1', 'scheme': 'https', 'server': ('testserver', 443), 'client': ('10.0.0.1', 1),
                 'root_path': ''}
        sent, gone = [], asyncio.Event()
        messages = [{'type': 'http.request', 'body': body, 'more_body': False}]

        async def receive():
            if messages:
                return messages.pop(0)
            if disconnect_after is not None:
                await asyncio.sleep(disconnect_after)
                return {'type': 'http.disconnect'}
            await gone.wait()
            return {'type': 'http.disconnect'}

        async def send(message):
            sent.append(message)
        await app(scope, receive, send)
        gone.set()
        start = next((m for m in sent if m['type'] == 'http.response.start'), None)
        if start is None:
            return None, {}, b''
        data = b''.join(m.get('body', b'') for m in sent if m['type'] == 'http.response.body')
        return start['status'], {k.decode(): v.decode() for k, v in start['headers']}, data
    return asyncio.run(go())


@override_settings(API_KEY='test-key', ALLOWED_HOSTS=['testserver'], SECURE_SSL_REDIRECT=False)
class AsgiStackTests(TransactionTestCase):
    def setUp(self):
        from attnd.asgi import application
        self.app = application
        self.user = User.objects.create_user('o', 'o@example.com', 'pw')
        self.event = Event.objects.create(name='Fair', event_code='FAIR1', start_time=timezone.now(), user=self.user,
                                          status=Event.STATUS_PUBLISHED)
        Device.objects.create(id='phone-1', event=self.event, platform='ios', preview_by=self.user,
                              preview_until=timezone.now() + timedelta(hours=1))
        self.version = Event.objects.get(pk=self.event.pk).content_version
        self.auth = [('Authorization', 'Bearer test-key'), ('X-Device-Id', 'phone-1')]

    def changes(self, since, headers=None, **kw):
        return call(self.app, f'/api/v2/events/{self.event.pk}/changes/', f'since={since}',
                    headers if headers is not None else self.auth, **kw)

    def test_plain_requests_go_through_django(self):
        status, headers, body = call(self.app, '/health/')
        self.assertEqual(status, 200)
        status, _, body = call(self.app, f'/api/v2/events/{self.event.pk}/bundle/',
                               headers=[('Authorization', 'Bearer test-key')])
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)['event']['name'], 'Fair')

    def test_preview_phone_waits_on_the_loop_and_wakes_on_a_change(self):
        @longpoll_asgi._closing
        def bump():
            time.sleep(1.0)
            Event.objects.filter(pk=self.event.pk).update(content_version=self.version + 1)
        threading.Thread(target=bump).start()
        started = time.monotonic()
        status, _, body = self.changes(self.version)
        took = time.monotonic() - started
        data = json.loads(body)
        self.assertEqual(status, 200)
        self.assertEqual((data['changed'], data['preview'], data['retry_after']), (True, True, 0))
        self.assertGreater(took, 0.9)
        self.assertLess(took, 5)

    def test_timeout_answers_unchanged(self):
        with patch.object(longpoll_asgi, 'CHANGES_SECONDS', 0.6):
            data = json.loads(self.changes(self.version)[2])
        self.assertEqual((data['changed'], data['retry_after']), (False, 0))

    def test_who_may_wait(self):
        with patch.object(longpoll_asgi.Waiters, 'wait', side_effect=AssertionError('must not wait')):
            # Not a preview phone, no key, a bad key, already changed: Django answers at once.
            data = json.loads(self.changes(self.version, [('Authorization', 'Bearer test-key'),
                                                          ('X-Device-Id', 'someone')])[2])
            self.assertEqual(data['preview'], False)
            self.assertEqual(self.changes(self.version, [('X-Device-Id', 'phone-1')])[0], 401)
            self.assertEqual(self.changes(self.version, [('Authorization', 'Bearer nope'),
                                                         ('X-Device-Id', 'phone-1')])[0], 401)
            self.assertTrue(json.loads(self.changes(self.version - 1)[2])['changed'])

    def test_a_client_cannot_claim_it_waited(self):
        with patch.object(longpoll_asgi, 'CHANGES_SECONDS', 0.6):
            started = time.monotonic()
            self.changes(self.version, [*self.auth, ('X-Attnd-Waited', 'anything')])
            self.assertGreater(time.monotonic() - started, 0.5)  # it waited: the header was dropped

    def test_a_phone_that_goes_away_gets_nothing(self):
        status, _, _ = self.changes(self.version, disconnect_after=0.3)
        self.assertIsNone(status)
        self.assertEqual(len(self.app.waiters), 0)

    def test_over_the_cap_falls_back_to_django(self):
        with patch.object(longpoll_asgi, 'MAX_WAITING', 0), patch('events.preview.wait_for_change',
                                                                     return_value=(self.version, False, False)):
            data = json.loads(self.changes(self.version)[2])
        self.assertEqual(data['retry_after'], 15)  # the view's own answer: poll again in 15 s

    def test_agent_run_waits_only_for_its_owner(self):
        from django.conf import settings
        from django.contrib.auth import BACKEND_SESSION_KEY, HASH_SESSION_KEY, SESSION_KEY
        from importlib import import_module

        from agent.models import AgentRun, AgentStep
        run = AgentRun.objects.create(user=self.user, goal='x', status=AgentRun.RUNNING)
        other = User.objects.create_user('p', 'p@example.com', 'pw')

        def session_for(user):
            store = import_module(settings.SESSION_ENGINE).SessionStore()
            store[SESSION_KEY] = str(user.pk)
            store[BACKEND_SESSION_KEY] = settings.AUTHENTICATION_BACKENDS[0]
            store[HASH_SESSION_KEY] = user.get_session_auth_hash()
            store.create()
            return store.session_key

        mine = longpoll_asgi.check_agent_run(run.pk, 0, session_for(self.user))
        self.assertEqual(mine['state'], (AgentRun.RUNNING, 0))
        self.assertIsNone(longpoll_asgi.check_agent_run(run.pk, 0, session_for(other)))
        self.assertIsNone(longpoll_asgi.check_agent_run(run.pk, 0, 'not-a-session'))
        AgentStep.objects.create(run=run, seq=1, kind=AgentStep.KIND_NOTE)
        self.assertIsNone(longpoll_asgi.check_agent_run(run.pk, 0, session_for(self.user)))  # news already

        # A new step wakes the waiter.
        waiters = longpoll_asgi.Waiters()

        async def scenario():
            async def add_step():
                await asyncio.sleep(0.6)
                await asyncio.to_thread(longpoll_asgi._closing(AgentStep.objects.create), run=run, seq=2,
                                        kind=AgentStep.KIND_NOTE)
            asyncio.ensure_future(add_step())
            return await waiters.wait('agent', run.pk, {'state': (AgentRun.RUNNING, 0), 'after': 1}, 5)
        started = time.monotonic()
        self.assertTrue(asyncio.run(scenario()))
        self.assertLess(time.monotonic() - started, 3)
