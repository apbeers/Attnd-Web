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


class ProposalTests(HarnessCase):
    def test_a_bulk_shift_is_previewed_then_applied_in_one_undo(self):
        from django.urls import reverse

        from agent.models import ChangeProposal
        from console.models import UndoEntry
        from events.models import ScheduledItem
        second = ScheduledItem.objects.create(event=self.event, name='Night Owls', location=self.stage,
                                              start_time=self.start + timedelta(hours=2))
        fmt = '%Y-%m-%dT%H:%M'
        import zoneinfo
        tz = zoneinfo.ZoneInfo('America/Chicago')
        changes = [{'tool': 'edit_schedule_item', 'label': it.name, 'arguments_json': json.dumps(self.args(
            'edit_schedule_item', event_id=self.event.pk, item_id=it.pk,
            start_time=(it.start_time + timedelta(minutes=30)).astimezone(tz).strftime(fmt)))}
            for it in (self.item, second)]
        run, provider = self.run_with([[('propose_changes', {'event_id': self.event.pk, 'title': 'Move sets 30 min later',
                                                             'changes': changes})],
                                       [('finish', {'summary': 'Here is the preview.', 'suggest': None})]])
        proposal = ChangeProposal.objects.get()
        self.assertEqual(len(proposal.changes), 2)
        self.assertEqual(proposal.changes[0]['rows'][0][0], 'start time')
        self.item.refresh_from_db()
        self.assertEqual(self.item.start_time, self.original_start)  # nothing changed yet
        self.assertEqual(harness.check(harness.by_name('bulk_shift'), self.case(run)), [])
        card = self.client.get(reverse('agent:component_render'), {
            'component': 'change_preview', 'items': json.dumps([{'event_id': self.event.pk, 'proposal_id': proposal.pk}])},
            secure=True)
        self.assertContains(card, 'Move sets 30 min later')
        undo_before = UndoEntry.objects.count()
        response = self.client.post(reverse('agent:proposal_apply', args=[proposal.pk]), {'keep': ['0', '1']},
                                    HTTP_X_ATTND_COMPONENT='change_preview', secure=True)
        self.assertEqual(response.json()['messages'], ['Applied 2 changes.'])
        self.item.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(self.item.start_time - self.original_start, timedelta(minutes=30))
        self.assertEqual(second.start_time - self.start, timedelta(hours=2, minutes=30))
        self.assertEqual(UndoEntry.objects.count(), undo_before + 1)  # one Undo for both
        undo = self.client.post(reverse('console:undo', args=[response.json()['undo']]), secure=True)
        self.assertLess(undo.status_code, 400)
        self.item.refresh_from_db()
        self.assertEqual(self.item.start_time, self.original_start)
        again = self.client.post(reverse('agent:proposal_apply', args=[proposal.pk]), {'keep': ['0']}, secure=True)
        self.assertEqual(again.status_code, 409)

    def test_always_ask_actions_and_other_events_are_refused(self):
        changes = [{'tool': 'delete_location', 'label': 'x', 'arguments_json': json.dumps(
            self.args('delete_location', event_id=self.event.pk, location_id=self.stage.pk))}]
        run, provider = self.run_with([[('propose_changes', {'event_id': self.event.pk, 'title': 'Clean up',
                                                             'changes': changes})],
                                       [('finish', {'summary': 'ok', 'suggest': None})]])
        self.assertIn('always asks', provider.results[0][1])


class ConversationTests(HarnessCase):
    def test_the_latest_conversation_comes_back_on_another_device(self):
        from django.urls import reverse
        old = self.make_run(goal='Older chat')
        AgentRun.objects.filter(pk=old.pk).update(thread='a' * 32)
        runs = [self.make_run(goal=f'Request {n}') for n in range(23)]
        AgentRun.objects.filter(pk__in=[r.pk for r in runs]).update(thread='b' * 32)
        data = self.client.get(reverse('agent:runs'), {'chat': '', 'latest': '1', 'event': self.event.pk}).json()
        self.assertEqual(data['thread'], 'b' * 32)
        self.assertEqual([c['run']['goal'] for c in data['chat']][-1], 'Request 22')
        self.assertEqual(len(data['chat']), 20)
        self.assertEqual(len(data['earlier']), 3)
        self.assertNotIn(old.pk, data['earlier'])
        # a new request joins the thread the browser names, only when it is the person's own
        with self.settings(AGENT_DAILY_SPEND_CAP_USD=2.0):
            from unittest import mock
            with mock.patch('agent.loop.spawn'):
                r = self.client.post(reverse('agent:start'), {'goal': 'More', 'thread': 'b' * 32}).json()['run']
                self.assertEqual(r['thread'], 'b' * 32)
                AgentRun.objects.filter(pk=r['id']).update(status='done')
                r2 = self.client.post(reverse('agent:start'), {'goal': 'More', 'thread': 'c' * 32}).json()['run']
                self.assertNotEqual(r2['thread'], 'c' * 32)


class LiveOpsTests(HarnessCase):
    def test_running_late_previews_the_edit_with_a_drafted_notification(self):
        from django.urls import reverse

        from agent.models import ChangeProposal
        changes = [{'tool': 'edit_schedule_item', 'label': self.item.name, 'arguments_json': json.dumps(self.args(
            'edit_schedule_item', event_id=self.event.pk, item_id=self.item.pk,
            start_time=(self.item.start_time + timedelta(minutes=20)).astimezone(
                __import__('zoneinfo').ZoneInfo('America/Chicago')).strftime('%Y-%m-%dT%H:%M')))}]
        self.run_with([[('propose_changes', {'event_id': self.event.pk, 'title': 'Prairie Lights 20 min late',
                                             'changes': changes, 'notify_title': 'Prairie Lights starts 20 min late',
                                             'notify_body': 'New time: 20 minutes later at the Main Stage.',
                                             'notify_urgent': False})],
                       [('finish', {'summary': 'Preview ready.', 'suggest': None})]])
        proposal = ChangeProposal.objects.get()
        self.assertEqual(proposal.notification['title'], 'Prairie Lights starts 20 min late')
        response = self.client.post(reverse('agent:proposal_apply', args=[proposal.pk]), {'keep': ['0']},
                                    secure=True).json()
        self.assertEqual(response['next']['component'], 'notification_form')
        self.assertEqual(response['next']['fill']['urgent'], False)
        self.assertFalse(Notification.objects.exists())  # drafted, never sent by Apply


class MemoryTests(HarnessCase):
    def test_remembered_preferences_reach_the_next_request_and_can_be_removed(self):
        from django.urls import reverse

        from agent.prompts import context_block
        from console.models import ConsolePreference
        run, _ = self.run_with([[('remember', {'note': 'Call the big stage Main Stage', 'event_id': None})],
                                [('finish', {'summary': 'Noted.', 'suggest': None})]])
        self.assertIn('Call the big stage Main Stage', context_block(self.make_run()))
        note = ConsolePreference.notes_for(self.owner)[0]
        page = self.client.get(reverse('console:account'), secure=True)
        self.assertContains(page, 'Call the big stage Main Stage')
        self.client.post(reverse('console:account'), {'action': 'forget_note', 'note': note['id']}, secure=True)
        self.assertEqual(ConsolePreference.notes_for(self.owner), [])


class SearchEverythingTests(HarnessCase):
    def test_finds_text_in_details_items_and_notifications_of_the_persons_events_only(self):
        from agent.search_tool import search
        from django.contrib.auth.models import User
        from events.models import Event
        self.event.context = {'faq': [{'q': 'Where do I park?', 'a': 'Free parking in lot B.'}]}
        self.event.save()
        Notification.objects.create(event=self.event, title='Parking update', body='Lot B is full, use lot C.',
                                    send_time=self.start)
        other = User.objects.create_user('z', 'z@example.com', 'pw')
        Event.objects.create(name='Other parking fest', event_code='OTHERP', user=other, start_time=self.start)
        kinds = {r['kind'] for r in search(self.owner, 'parking')}
        self.assertEqual(kinds, {'event', 'notification'})
        self.assertTrue(all(r['event_id'] == self.event.pk for r in search(self.owner, 'parking')))
        self.assertEqual(search(self.owner, 'indie folk')[0]['item_id'], self.item.pk)


class LongPollTests(HarnessCase):
    def test_a_long_poll_answers_at_once_when_there_is_news_and_never_waits_in_tests_transactions(self):
        from django.urls import reverse
        run = self.make_run()
        data = self.client.get(reverse('agent:run', args=[run.pk]), {'after': 0, 'wait': 10}).json()
        self.assertFalse(data['waited'])  # tests run inside a transaction: never sleeps there
        from agent.views import _wait_for_news
        run.status = 'done'
        self.assertFalse(_wait_for_news(run, 0, '10'))  # finished runs answer at once


class ContactTests(HarnessCase):
    def test_talk_to_a_person_emails_the_team_with_context_but_no_secrets(self):
        from unittest import mock

        from django.core import mail
        from django.test import override_settings
        from django.urls import reverse
        run = self.make_run(goal='Publish my event')
        AgentRun.objects.filter(pk=run.pk).update(error='Something broke', status='failed')
        with override_settings(ADMINS=[('Team', 'team@example.com')],
                               EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend'), \
                mock.patch('console.mail.email_enabled', return_value=True):
            data = self.client.post(reverse('agent:contact'), {'message': 'It keeps failing', 'run': run.pk,
                                                               'page': '/console/'}).json()
            self.assertTrue(data['sent'])
            sent = mail.outbox[0]
            self.assertEqual(sent.to, ['team@example.com'])
            self.assertEqual(sent.reply_to, ['owner@example.com'])
            self.assertIn(f'Ask Attnd run: {run.pk}', sent.body)
            self.assertIn('Publish my event', sent.body)
            self.assertNotIn(run.session_key or 'no-session', sent.body)
            for _ in range(3):
                self.client.post(reverse('agent:contact'), {'message': 'again'})
            self.assertEqual(self.client.post(reverse('agent:contact'), {'message': 'again'}).status_code, 429)
