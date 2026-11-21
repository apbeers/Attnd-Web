"""Ask Attnd's quality harness (agent/harness.py).

Scripted runs check the checker: good behaviour passes, each kind of bad behaviour is caught. The live suite runs
every scenario against the real model only with AGENT_HARNESS_LIVE=1 and ANTHROPIC_API_KEY (CI: the nightly
agent-harness workflow); it writes a JSON report to AGENT_HARNESS_REPORT when set.
"""

import json
import os
import unittest
from datetime import timedelta
from types import SimpleNamespace

from django.conf import settings

from agent import harness, loop, registry
from agent.models import AgentRun
from agent.tests import AgentTestCase
from events.models import Notification

LIVE = os.getenv('AGENT_HARNESS_LIVE') == '1' and bool(getattr(settings, 'ANTHROPIC_API_KEY', ''))


class HarnessCase(AgentTestCase):
    def setUp(self):
        super().setUp()
        self.original_start = self.item.start_time

    def prepare(self, scenario):
        if scenario.multi_day:
            self.event.end_time = self.event.start_time + timedelta(days=1, hours=10)
            self.event.save()
        if scenario.injection:
            self.stage.description = 'The big stage by the river. ' + harness.INJECTION
            self.stage.save()
        return self.make_run(goal=scenario.goal)

    def case(self, run):
        run.refresh_from_db()
        return SimpleNamespace(run=run, event=self.event, stage=self.stage, item=self.item,
                               original_start=self.original_start)

    def args(self, action, **values):
        schema = registry.tool_schema(registry.registry()['writes'][action])['properties']
        return {**{k: None for k in schema}, **values}


class CheckerTests(HarnessCase):
    def test_a_good_add_location_passes_and_a_json_leak_fails(self):
        scenario = harness.by_name('add_location')
        run = self.prepare(scenario)
        run, _ = self.run_with([[('add_location', self.args('add_location', event_id=self.event.pk,
                                                            name='Lemonade Stand', category='Food'))],
                                [('finish', {'summary': 'Added Lemonade Stand.', 'suggest': None})]], run=run)
        self.assertEqual(harness.check(scenario, self.case(run)), [])
        AgentRun.objects.filter(pk=run.pk).update(summary='Done {"location_id": 4}')
        self.assertTrue(any('JSON' in p for p in harness.check(scenario, self.case(run))))

    def test_sending_stops_at_the_gate(self):
        scenario = harness.by_name('send_urgent')
        run = self.prepare(scenario)
        run, _ = self.run_with([[('send_notification', self.args(
            'send_notification', event_id=self.event.pk, title='Gates open late',
            body='The gates open 30 minutes late.', when='now', urgent=True))]], run=run)
        self.assertEqual(harness.check(scenario, self.case(run)), [])
        self.assertFalse(Notification.objects.filter(sent=True).exists())

    def test_ignoring_the_question_or_following_an_injection_fails(self):
        scenario = harness.by_name('ambiguous_day')
        run = self.prepare(scenario)
        run, _ = self.run_with([[('finish', {'summary': 'Which day did you mean?', 'suggest': None})]], run=run)
        problems = harness.check(scenario, self.case(run))
        self.assertTrue(any('expected one of' in p for p in problems))
        self.assertTrue(any('status done' in p for p in problems))

        scenario = harness.by_name('injection')
        run = self.prepare(scenario)
        run, _ = self.run_with([[('delete_location', self.args('delete_location', event_id=self.event.pk,
                                                               location_id=self.stage.pk))]], run=run)
        self.assertIn('called a forbidden tool: delete_location', harness.check(scenario, self.case(run)))

    def test_password_questions_never_open_account_pages(self):
        scenario = harness.by_name('password')
        run = self.prepare(scenario)
        run, _ = self.run_with([[('read_page', {'path': '/console/account/', 'offset': None})],
                                [('platform_help', {})],
                                [('finish', {'summary': 'Open Account and choose Change password.', 'suggest': None})]],
                               run=run)
        self.assertIn('called a forbidden tool: read_page:/console/account/', harness.check(scenario, self.case(run)))


@unittest.skipUnless(LIVE, 'live harness: set AGENT_HARNESS_LIVE=1 and ANTHROPIC_API_KEY')
class LiveHarnessTests(HarnessCase):
    """Every scenario against the real model (costs a few cents per run)."""
    report = []

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        path = os.getenv('AGENT_HARNESS_REPORT')
        if path:
            with open(path, 'w') as fh:
                json.dump(cls.report, fh, indent=2)

    def _live(self, name):
        scenario = harness.by_name(name)
        run = self.prepare(scenario)
        loop.execute(run.pk)
        case = self.case(run)
        problems = harness.check(scenario, case)
        self.report.append({'scenario': name, 'status': case.run.status, 'cost_usd': str(case.run.cost_usd),
                            'tools': [n for n, _, _ in harness._called(case.run)], 'problems': problems,
                            'summary': case.run.summary[:300]})
        self.assertEqual(problems, [], f'{name}: {problems}')


for _scenario in harness.SCENARIOS:
    setattr(LiveHarnessTests, f'test_{_scenario.name}', lambda self, n=_scenario.name: self._live(n))


class FeedbackTests(HarnessCase):
    def test_thumbs_are_stored_for_the_runs_owner_only(self):
        from django.urls import reverse
        run = self.make_run()
        url = reverse('agent:feedback', args=[run.pk])
        self.assertEqual(self.client.post(url, {'value': '-1', 'note': 'Wrong day'}).json()['run']['feedback'], -1)
        run.refresh_from_db()
        self.assertEqual((run.feedback, run.feedback_note), (-1, 'Wrong day'))
        self.assertEqual(self.client.post(url, {'value': '2'}).status_code, 400)
        from django.contrib.auth.models import User
        other = User.objects.create_user('x', 'x@example.com', 'pw')
        self.client.force_login(other)
        self.assertEqual(self.client.post(url, {'value': '1'}).status_code, 404)
