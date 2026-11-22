"""Batch 7 Track B: page ↔ chat parity. Every console write has a button or form in some component (so the chat and
the canvas offer it, not only the page), or is in TOOL_ONLY with its reason; every console page is drawn by components
or is a BESPOKE screen with its reason; docs/parity.md is the generated table (UPDATE_PARITY=1 rewrites it)."""
import datetime as dt
import os
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase, override_settings

from console import parity
from console.testing import TwoFactorClient
from events.models import Event

DOC = Path(settings.BASE_DIR) / 'docs' / 'parity.md'
# The table follows what the DEMO event's pages show (now / next, readiness), so it is made at one fixed moment:
# with the real clock it changed during the day and failed CI.
FIXED_NOW = dt.datetime(2026, 11, 14, 22, 0, tzinfo=dt.UTC)


@override_settings(SITE_URL='https://attnd.app')
class ParityTests(TestCase):
    @classmethod
    def setUpClass(cls):
        cls._clock = mock.patch('django.utils.timezone.now', return_value=FIXED_NOW)
        cls._clock.start()
        cls.addClassCleanup(cls._clock.stop)
        super().setUpClass()

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user('parity', 'parity@example.com', 'pw', is_staff=True, is_superuser=True)
        call_command('load_demo_event', '--owner', 'parity@example.com', '--seed', '7', stdout=open(os.devnull, 'w'))

    def test_parity(self):
        client = TwoFactorClient()
        client.force_login(self.user)
        event = Event.objects.get(user=self.user)
        rows = parity.table(self.user, event, client)
        self.assertEqual(parity.problems(rows), [], '\n'.join(parity.problems(rows)))
        tools = {w['tool'] for r in rows for w in r['writes']}
        stale = sorted((set(parity.TOOL_ONLY) | set(parity.SHOWN_WHEN)) - tools)
        self.assertEqual(stale, [], 'TOOL_ONLY entries for tools that no longer exist')
        reasons = [*parity.TOOL_ONLY.values(), *parity.SHOWN_WHEN.values(), *parity.BESPOKE.values(),
                   *parity.BESPOKE_AREAS.values()]
        from console import components as C
        for tool, where in parity.SHOWN_WHEN.items():  # the component named really exists
            self.assertIn(where.split(':')[0], C.registry(), tool)
        self.assertTrue(all(len(r) > 15 for r in reasons))
        text = parity.markdown(rows)
        if os.environ.get('UPDATE_PARITY'):
            DOC.write_text(text)
        self.assertEqual(DOC.read_text() if DOC.exists() else '', text,
                         'docs/parity.md is out of date: UPDATE_PARITY=1 uv run python manage.py test console.tests_parity')
