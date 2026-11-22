"""Friendly steps of background tasks (scrape.jobs.Steps): what the task tray shows when a task is opened."""

from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse

from events.models import Event
from scrape import jobs, service
from scrape.models import IngestJob
from scrape.tests import RunJobsInline, _sample_data


def fake_scrape(**kwargs):
    """Like scrape_event: reports steps through `progress` (3 pages read, the AI reading), then returns data."""
    progress = kwargs['progress']
    progress('Reading fest.example…', done='Couldn’t read any web pages')
    for n in (1, 2, 3):
        progress.update(f'Reading web pages: {n} so far (fest.example)…', done=f'Read {n} web page{"s" if n > 1 else ""}')
    progress('Finding the locations and the schedule…', done='Understood the event')
    return _sample_data()


class StepsTests(RunJobsInline, TestCase):
    def setUp(self):
        self.user = User.objects.create_user('o', 'o@example.com', 'pw')
        self.client = Client()
        self.client.force_login(self.user)

    def test_an_import_records_friendly_steps_and_the_feed_has_them(self):
        job = IngestJob.objects.create(kind=IngestJob.KIND_CREATE, urls=['https://fest.example/'], created_by=self.user)
        tasks = self.client.get(reverse('console:tasks'), HTTP_ACCEPT='application/json', secure=True).json()['tasks']
        self.assertEqual(tasks[0]['steps'], [{'text': 'Waiting to start…', 'state': 'running'}])
        with patch('scrape.jobs.scrape_event', side_effect=fake_scrape):
            jobs.run_job(job.pk)
        job.refresh_from_db()
        texts = [s['text'] for s in job.steps]
        self.assertEqual(texts[:3], ['Read 3 web pages', 'Understood the event', 'Found 2 locations and 2 schedule items'])
        self.assertTrue(texts[-1].startswith('Created “'))
        self.assertTrue(all(s['state'] == 'done' for s in job.steps))
        task = self.client.get(reverse('console:tasks'), secure=True).json()['tasks'][0]
        self.assertEqual([s['text'] for s in task['steps']], texts)
        self.assertEqual(task['url'], reverse('console:event', args=[job.event_id]))
        self.assertEqual(task['result_label'], 'Open the event')

    def test_a_running_step_is_the_progress_and_a_failure_marks_it(self):
        job = IngestJob.objects.create(kind=IngestJob.KIND_CREATE, urls=['https://fest.example/'], created_by=self.user)

        def boom(**kwargs):
            kwargs['progress']('Finding the locations and the schedule…', done='Understood the event')
            self.assertEqual(IngestJob.objects.get(pk=job.pk).progress, 'Finding the locations and the schedule…')
            raise ValueError('No content could be retrieved from that URL.')
        with patch('scrape.jobs.scrape_event', side_effect=boom):
            jobs.run_job(job.pk)
        job.refresh_from_db()
        self.assertEqual(job.status, IngestJob.FAILED)
        self.assertEqual(job.steps[-1], {**job.steps[-1], 'state': 'failed',
                                         'text': 'Finding the locations and the schedule…'})

    def test_a_refresh_that_finds_changes_waits_for_review(self):
        event = Event.objects.create(name='Summer Fair', event_code='SUMMER', user=self.user,
                                     start_time='2026-07-04T18:00:00Z', source_urls=['https://fest.example/'],
                                     update_mode=Event.UPDATE_REVIEW)
        job = IngestJob.objects.create(kind=IngestJob.KIND_REFRESH, event=event, created_by=self.user)
        with patch('scrape.jobs.scrape_event', return_value=_sample_data()):
            jobs.run_job(job.pk)
        job.refresh_from_db()
        self.assertEqual(job.steps[-1]['state'], 'waiting')
        self.assertIn('waiting for your review', job.steps[-1]['text'])
        self.assertIn('Compared with your event', [s['text'] for s in job.steps])
        task = self.client.get(reverse('console:tasks'), secure=True).json()['tasks'][0]
        self.assertRegex(task['result_label'], r'^Review \d+ changes?$')

    def test_scrape_event_reports_pages_read(self):
        steps = []

        class Progress:
            def __call__(self, text, done=None):
                steps.append([text, done])

            def update(self, text, done=None):
                steps[-1] = [text, done]
        with patch.object(service, 'gather_sources', side_effect=lambda urls, **kw: [kw['on_page'](u) for u in ('a', 'b')] and '## Source: a\n\nhi'), \
                patch.object(service, '_extract', return_value={'name': 'x'}):
            service.scrape_event(urls=['https://fest.example/'], progress=Progress())
        self.assertEqual(steps[0][1], 'Read 2 web pages')
        self.assertEqual(steps[-1], ['Finding the locations and the schedule…', 'Understood the event'])
