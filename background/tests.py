"""Stage F5: django.tasks on our database queue: enqueue, claim, run, results, retries, the worker command."""

from contextlib import contextmanager
from datetime import timedelta
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.tasks import TaskResultStatus, task, task_backends
from django.test import TestCase, override_settings
from django.utils import timezone

import background
from background.backend import claim, requeue_stale
from background.models import QueuedTask

DB = {'default': {'BACKEND': 'background.backend.DatabaseBackend'}}
CALLS = []


@task
def add(a, b):
    CALLS.append((a, b))
    return a + b


@task(priority=10)
def urgent():
    return 'first'


@task
def boom():
    raise ValueError('nope')


def _reset():
    task_backends.__dict__.pop('settings', None)
    task_backends._settings = None
    for alias in ('default',):
        try:
            del task_backends[alias]
        except AttributeError:
            pass


@contextmanager
def database_tasks():
    with override_settings(TASKS=DB):
        _reset()
        try:
            yield
        finally:
            _reset()


class QueueTests(TestCase):
    def setUp(self):
        CALLS.clear()

    def test_off_by_default_runs_the_fallback(self):
        self.assertFalse(background.database_queue())
        seen = []
        self.assertIsNone(background.enqueue(add, 1, 2, fallback=lambda a, b: seen.append(a + b)))
        self.assertEqual(seen, [3])

    def test_enqueue_and_the_worker_runs_by_priority(self):
        with database_tasks():
            self.assertTrue(background.database_queue())
            first = add.enqueue(2, 3)
            later = urgent.enqueue()
            deferred = add.using(run_after=timezone.now() + timedelta(hours=1)).enqueue(9, 9)
            self.assertEqual(first.status, TaskResultStatus.READY)
            self.assertEqual(CALLS, [])
            out = StringIO()
            call_command('run_tasks', once=True, stdout=out)
            self.assertIn('Ran 2 task(s)', out.getvalue())
            first.refresh()
            later.refresh()
            self.assertEqual(first.return_value, 5)
            self.assertEqual(later.return_value, 'first')
            self.assertLess(QueuedTask.objects.get(pk=later.id).started_at, QueuedTask.objects.get(pk=first.id).started_at)
            deferred.refresh()
            self.assertEqual(deferred.status, TaskResultStatus.READY)
            self.assertEqual(CALLS, [(2, 3)])

    def test_failures_are_recorded_and_lost_workers_retried(self):
        with database_tasks():
            result = boom.enqueue()
            call_command('run_tasks', once=True, stdout=StringIO())
            result.refresh()
            self.assertEqual(result.status, TaskResultStatus.FAILED)
            self.assertEqual(result.errors[0].exception_class_path, 'builtins.ValueError')
            stuck = add.enqueue(1, 1)
            claim('dead-worker')
            QueuedTask.objects.filter(pk=stuck.id).update(last_attempted_at=timezone.now() - timedelta(hours=1))
            self.assertEqual(requeue_stale(), 1)
            self.assertEqual(QueuedTask.objects.get(pk=stuck.id).status, TaskResultStatus.READY)

    def test_place_images_go_through_the_queue_when_it_is_on(self):
        from django.contrib.auth.models import User

        from events.models import Event, Location
        owner = User.objects.create_user('o', 'o@example.com', 'pw')
        event = Event.objects.create(name='Fest', event_code='FESTQ', user=owner, start_time=timezone.now())
        with database_tasks(), override_settings(PLACE_IMAGES_ON_CREATE=True), \
                self.captureOnCommitCallbacks(execute=True):
            loc = Location.objects.create(event=event, name='Taco Row')
        row = QueuedTask.objects.get()
        self.assertEqual((row.task_path, row.args), ('events.tasks.find_place_image', [loc.pk]))
        with database_tasks(), mock.patch('events.place_images.find_for', return_value='none') as find:
            call_command('run_tasks', once=True, stdout=StringIO())
        find.assert_called_once()
        self.assertEqual(QueuedTask.objects.get().return_value, 'none')
