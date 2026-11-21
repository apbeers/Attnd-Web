"""Ask Attnd full screen (/console/assistant/), follow-up suggestions, the thinking line's stage text."""
import json

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from agent import registry
from agent.models import AgentRun, AgentStep
from agent.tests import AgentTestCase
from agent.views import stage_text


class FullScreenTests(AgentTestCase):
    def test_page(self):
        url = reverse('console:assistant') + f'?event={self.event.pk}&from=/console/events/{self.event.pk}/locations/'
        page = self.client.get(url, secure=True).content.decode()
        self.assertIn('data-mode="full"', page)
        self.assertIn(f'data-event-id="{self.event.pk}"', page)
        self.assertIn(f'data-from="/console/events/{self.event.pk}/locations/"', page)
        self.assertIn('data-canvas', page)
        self.assertIn('data-canvas-overlay', page)
        self.assertIn('data-agent-exit', page)
        self.assertEqual(page.count('data-agent '), 1)  # only the full panel, not the floating one too
        self.assertIn('agent/agent.css', page)

    def test_from_and_event_are_checked(self):
        stranger = User.objects.create_user('s', 's@example.com', 'pw')
        self.client.force_login(stranger)
        page = self.client.get(reverse('console:assistant') + f'?event={self.event.pk}&from=https://evil.example/',
                               secure=True).content.decode()
        self.assertIn('data-event-id=""', page)
        self.assertIn('data-from="/console/"', page)
        page = self.client.get(reverse('console:assistant') + '?from=/console/account/', secure=True).content.decode()
        self.assertIn('data-from="/console/"', page)

    def test_registry_and_off_limits(self):
        self.assertIn('console:assistant', registry.EXCLUDED)
        self.assertTrue(registry.off_limits('/console/assistant/'))

    def test_floating_panel_has_the_full_screen_button(self):
        page = self.client.get(reverse('console:locations', args=[self.event.pk]), secure=True).content.decode()
        self.assertIn('data-agent-full', page)
        self.assertIn('data-agent-thinking', page)
        self.assertIn('data-agent-suggest', page)

    def test_component_cards_carry_their_detail_ids(self):
        import json
        data = self.client.get(reverse('agent:component_render'), {
            'component': 'location_row', 'items': json.dumps([{'event_id': self.event.pk}])}, secure=True).json()
        item = data['items'][0]
        self.assertEqual(item['detail'], 'location_form')
        self.assertIn('location_id', item['detail_ids'])


class SuggestionTests(AgentTestCase):
    def run_json(self, run):
        return self.client.get(reverse('agent:run', args=[run.pk]), secure=True).json()['run']

    def test_finish_suggestions(self):
        run, _ = self.run_with([[('finish', {'summary': 'Done.', 'suggest': ['Show the schedule', 'Add a location']})]])
        self.assertEqual(self.run_json(run)['suggestions'], ['Show the schedule', 'Add a location'])

    def test_derived_from_the_last_component(self):
        run, _ = self.run_with([[('show', {'component': 'location_row', 'items': [{'event_id': self.event.pk}]})],
                                'Here they are.'])
        self.assertEqual(self.run_json(run)['suggestions'][0], 'Add a location')

    def test_none_while_active(self):
        run, _ = self.run_with([[('ask_user', {'question': 'Which?', 'options': None})]])
        self.assertEqual(self.run_json(run)['suggestions'], [])


class StageTextTests(AgentTestCase):
    def step(self, **fields):
        run = self.make_run()
        return AgentStep(run=run, seq=1, **fields)

    def test_stages(self):
        self.assertEqual(stage_text(self.step(tool='read_page', arguments={'path': f'/console/events/{self.event.pk}/notifications/'})),
                         'Reading Updates…')
        self.assertEqual(stage_text(self.step(tool='read_page', arguments={'path': f'/console/events/{self.event.pk}/schedule/'})),
                         'Reading the schedule…')
        self.assertEqual(stage_text(self.step(tool='add_location', arguments={'name': 'Taco Row'})),
                         'Adding location “Taco Row”…')
        self.assertEqual(stage_text(self.step(tool='send_notification', arguments={'title': 'Starting soon'})),
                         'Sending notification “Starting soon”…')
        self.assertEqual(stage_text(self.step(kind=AgentStep.KIND_COMPONENT, tool='show', text='Schedule')),
                         'Showing Schedule…')

    def test_step_json_has_the_stage(self):
        run, _ = self.run_with([[('read_page', {'path': f'/console/events/{self.event.pk}/', 'offset': None})], 'ok'])
        steps = self.client.get(reverse('agent:run', args=[run.pk]), secure=True).json()['steps']
        self.assertEqual(steps[0]['stage'], 'Reading the overview…')
        self.assertEqual(run.status, AgentRun.DONE)


class TalkToAPersonTests(TestCase):
    """Batch 5 (owner, Nov 21, 2026): Talk to a person comes after a 👎 (with its note), not above the composer or as a
    standing chip; the contact endpoint and the /contact command stay."""

    def test_not_standing(self):
        from pathlib import Path

        from django.conf import settings

        from agent import views
        from agent.models import AgentRun
        root = Path(settings.BASE_DIR)
        self.assertNotIn('data-agent-person', (root / 'agent/templates/agent/_panel.html').read_text())
        js = (root / 'agent/static/agent/agent.js').read_text()
        self.assertIn("key: 'contact'", js)
        self.assertIn('notHelpful(row, r)', js)
        user = User.objects.create_user('p', 'p@example.com', 'pw')
        run = AgentRun.objects.create(user=user, goal='x', status=AgentRun.DONE,
                                      suggestions=['Show the schedule', 'Talk to a person'])
        self.assertEqual(views.suggestions(run), ['Show the schedule'])
        from django.urls import reverse
        self.assertTrue(reverse('agent:contact'))


class TaskCardTests(TestCase):
    """Batch 5: a background job started in the conversation is a live card at that step (the step's JSON carries
    the task id); the feed answers for those ids whatever their age; a failed task can run again."""

    def setUp(self):
        from events.models import Event
        from django.utils import timezone
        self.user = User.objects.create_user('t', 't@example.com', 'pw')
        self.event = Event.objects.create(name='E', event_code='TASK1', user=self.user, start_time=timezone.now(),
                                          source_urls=['https://example.com/'])
        self.client.force_login(self.user)

    def test_step_feed_and_retry(self):
        from datetime import timedelta
        from unittest.mock import patch

        from django.utils import timezone

        from agent.views import step_json
        from scrape.models import IngestJob
        job = IngestJob.objects.create(kind=IngestJob.KIND_REFRESH, event=self.event, created_by=self.user,
                                       status=IngestJob.FAILED, error='The AI couldn’t read those sources.',
                                       finished_at=timezone.now() - timedelta(hours=3))
        run = AgentRun.objects.create(user=self.user, goal='check for updates')
        step = AgentStep.objects.create(run=run, seq=1, tool='check_for_updates', changed=True,
                                        result=json.dumps({'task': {'id': str(job.pk), 'title': 'x'}}))
        self.assertEqual(step_json(step)['task'], str(job.pk))
        feed = self.client.get('/console/tasks/', secure=True).json()['tasks']
        self.assertEqual(feed, [])  # three hours old: not in the tray
        (t,) = self.client.get(f'/console/tasks/?ids={job.pk},nonsense', secure=True).json()['tasks']
        self.assertEqual(t['status'], 'failed')
        self.assertTrue(t['retry_url'])
        with patch('scrape.jobs.enqueue') as enqueue:
            again = self.client.post(t['retry_url'], secure=True)
        self.assertEqual(again.status_code, 202)
        enqueue.assert_called_once()
        self.assertEqual(again.json()['task']['status'], 'queued')
        other = User.objects.create_user('o', 'o@example.com', 'pw')
        self.client.force_login(other)
        self.assertEqual(self.client.post(t['retry_url'], secure=True).status_code, 404)
