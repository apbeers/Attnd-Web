"""One chat (Nov 22, 2026): phones use the full-screen chat page, never a bottom sheet.

Always: the session-jump regressions (a new request while an older one waits for a confirmation or an answer
supersedes it instead of being refused and swapping the log for that other run; restoring a conversation keeps its
own thread even when an active run of another conversation is appended), and no phone-only chat code paths remain
(no bottom sheet, no visualViewport sizing).

With a browser (ATTND_BROWSER_CHECKS=1, Chrome and the `playwright` npm package; not in CI): scripts/ci/chat_check.cjs
drives the chat on an emulated iPhone 15 Pro and a 1440 × 900 desktop (open, send, keyboard, history, close and
reopen). ATTND_CHAT_SHOTS=<dir> (and ATTND_CHAT_LABEL) saves screenshots there."""
import json
import os
import subprocess
import time
import unittest
from datetime import timedelta
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.contrib.auth.models import User
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from agent.models import AgentRun, AgentStep
from console.testing import TwoFactorClient
from console.tests_phone import _playwright
from events.models import Event
from studio.ai import Turn

ROOT = Path(settings.BASE_DIR)
JS = (ROOT / 'agent/static/agent/agent.js').read_text()
CSS = (ROOT / 'agent/static/agent/agent.css').read_text()
LOADER = (ROOT / 'agent/static/agent/agent-loader.js').read_text()


class AnswerProvider:
    """Answers every request with one short sentence after a moment (the run is visibly working first)."""
    name = 'anthropic'
    model = 'claude-haiku-5-5'
    effort = 'medium'

    def start(self, text):
        return [{'role': 'user', 'content': text}]

    def state(self):
        return {}

    def restore(self, state):
        pass

    def chat(self, system, messages, tools):
        time.sleep(0.6)
        return Turn(text='Main Stage and Taco Row have nothing on the schedule yet.', input_tokens=100,
                    output_tokens=20, model=self.model)

    def add_turn(self, messages, turn):
        messages.append({'role': 'assistant', 'content': turn.text})

    def add_results(self, messages, results):
        messages.append({'role': 'tool', 'content': [t for _, t in results]})


@override_settings(ALLOWED_HOSTS=['testserver', 'localhost'], SECURE_SSL_REDIRECT=False, AGENT_DAILY_SPEND_CAP_USD=2.0)
class SessionJumpTests(TestCase):
    """"Half the time when I hit send, it jumps to some other session where it doesn't even show the thing I just
    did": a send while an older run waited (for a confirmation the phone sheet hid, or an answer) was refused with 409
    and the panel replaced the whole log with that other run. Now the waiting run is stopped and the new request
    starts in the conversation the browser is showing."""

    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user('jump', 'jump@example.com', 'pw')
        self.client = TwoFactorClient()
        self.client.force_login(self.user)

    def _run(self, status, thread, goal='Earlier request'):
        return AgentRun.objects.create(user=self.user, goal=goal, status=status, thread=thread)

    def test_a_new_request_supersedes_a_run_waiting_for_confirmation(self):
        waiting = self._run(AgentRun.WAITING_CONFIRMATION, 'a' * 32)
        AgentStep.objects.create(run=waiting, seq=1, kind=AgentStep.KIND_TOOL, tool='delete_location',
                                 confirm_state=AgentStep.CONFIRM_PENDING, confirm_summary='Delete Main Stage')
        mine = self._run(AgentRun.DONE, 'b' * 32)
        with mock.patch('agent.loop.spawn'):
            response = self.client.post(reverse('agent:start'), {'goal': 'Add a location', 'thread': mine.thread})
        self.assertEqual(response.status_code, 202, response.content)
        new = response.json()['run']
        self.assertEqual(new['thread'], mine.thread)  # the conversation on screen, not the waiting one's
        waiting.refresh_from_db()
        self.assertEqual(waiting.status, AgentRun.STOPPED)
        self.assertEqual(waiting.steps.get().confirm_state, AgentStep.CONFIRM_EXPIRED)  # nothing it asked can run now

    def test_a_new_request_supersedes_a_run_waiting_for_an_answer(self):
        waiting = self._run(AgentRun.WAITING_REPLY, 'c' * 32)
        with mock.patch('agent.loop.spawn'):
            response = self.client.post(reverse('agent:start'), {'goal': 'Something else', 'thread': waiting.thread})
        self.assertEqual(response.status_code, 202)
        waiting.refresh_from_db()
        self.assertEqual(waiting.status, AgentRun.STOPPED)

    def test_a_run_still_working_is_still_refused(self):
        self._run(AgentRun.RUNNING, 'd' * 32)
        response = self.client.post(reverse('agent:start'), {'goal': 'Another'})
        self.assertEqual(response.status_code, 409)

    def test_restoring_keeps_the_conversations_own_thread(self):
        mine = [self._run(AgentRun.DONE, 'e' * 32, f'Mine {i}') for i in range(2)]
        other = self._run(AgentRun.WAITING_REPLY, 'f' * 32, 'In another tab')
        data = self.client.get(reverse('agent:runs') + f'?chat={mine[0].pk},{mine[1].pk}').json()
        self.assertEqual([c['run']['id'] for c in data['chat']], [mine[0].pk, mine[1].pk, other.pk])
        self.assertEqual(data['thread'], 'e' * 32)  # not the appended active run's thread


class OneChatTests(TestCase):
    """No phone-only chat code: the floating panel is for wide screens, phones open the full-screen page."""

    def test_no_bottom_sheet_left(self):
        for text in (JS, CSS, LOADER):
            for gone in ('agent-sheet', 'fitSheet', '--agent-sheet'):
                self.assertNotIn(gone, text)
        self.assertNotIn('(max-width: 600px)', JS)  # agent.js has no phone branch: the loader routes phones
        self.assertEqual(JS.count('visualViewport'), 1)  # the chat page's height, every width alike

    def test_phones_go_to_the_full_screen_page(self):
        self.assertIn('attndOpenChat', LOADER)
        self.assertIn('PHONE', LOADER)
        self.assertIn('window.attndOpenChat', JS)  # agent.js opens through the same function


@unittest.skipUnless(_playwright(), 'ATTND_BROWSER_CHECKS=1 with Chrome and the playwright npm package')
@override_settings(ANTHROPIC_API_KEY='test', AGENT_DAILY_SPEND_CAP_USD=5.0)
class ChatBrowserTests(StaticLiveServerTestCase):
    def test_one_chat_on_a_phone_and_a_desktop(self):
        user = User.objects.create_user('chat', 'chat@example.com', 'pw')
        call_command('load_demo_event', '--owner', 'chat@example.com', stdout=open(os.devnull, 'w'))
        event = Event.objects.get(user=user)
        AgentRun.objects.create(user=user, goal='Add the schedule tab back', status=AgentRun.FAILED, thread='9' * 32,
                                error='That didn’t work.', event=event,
                                created_at=timezone.now() - timedelta(hours=2))
        client = TwoFactorClient()
        client.force_login(user)
        session = client.cookies[settings.SESSION_COOKIE_NAME].value
        shots = os.environ.get('ATTND_CHAT_SHOTS', '')
        if shots:
            Path(shots).mkdir(parents=True, exist_ok=True)
        with mock.patch('agent.loop._provider', return_value=AnswerProvider()):
            result = subprocess.run(['node', str(ROOT / 'scripts/ci/chat_check.cjs'), self.live_server_url, session,
                                     str(event.pk), shots, os.environ.get('ATTND_CHAT_LABEL', 'after')],
                                    capture_output=True, text=True, timeout=300,
                                    env={**os.environ, 'NODE_PATH': _playwright()})
        self.assertEqual(result.returncode, 0, result.stderr[-3000:])
        report = json.loads(result.stdout)
        print('\n'.join(report['notes']))
        self.assertEqual(report['problems'], [])
