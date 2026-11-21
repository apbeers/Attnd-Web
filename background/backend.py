"""A django.tasks backend that stores tasks in Postgres (`QueuedTask`) for `manage.py run_tasks` to run.

    TASKS = {'default': {'BACKEND': 'background.backend.DatabaseBackend'}}

Enqueuing inside a transaction makes the task visible when the transaction commits (it is a row like any other), so a
task never runs before the data it needs exists. Workers claim tasks with `SELECT … FOR UPDATE SKIP LOCKED`, so any
number of them (a worker dyno, Heroku Scheduler's `run_tasks --once`) can share the queue.
"""

import logging
import socket
from datetime import timedelta
from traceback import format_exception

from django.db import transaction
from django.tasks import TaskContext, TaskResult, TaskResultStatus
from django.tasks.backends.base import BaseTaskBackend
from django.tasks.base import TaskError
from django.tasks.exceptions import TaskResultDoesNotExist
from django.tasks.signals import task_enqueued, task_finished, task_started
from django.utils import timezone
from django.utils.crypto import get_random_string
from django.utils.json import normalize_json
from django.utils.module_loading import import_string

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
STALE_AFTER = timedelta(minutes=30)  # a RUNNING task whose worker died goes back to the queue


class DatabaseBackend(BaseTaskBackend):
    supports_defer = True
    supports_get_result = True
    supports_priority = True

    def enqueue(self, task, args, kwargs):
        from background.models import QueuedTask
        self.validate_task(task)
        row = QueuedTask.objects.create(
            id=get_random_string(32), task_path=task.module_path, queue_name=task.queue_name, priority=task.priority,
            args=normalize_json(list(args)), kwargs=normalize_json(dict(kwargs)), run_after=task.run_after)
        result = self._result(row, task)
        task_enqueued.send(type(self), task_result=result)
        return result

    def get_result(self, result_id):
        from background.models import QueuedTask
        row = QueuedTask.objects.filter(pk=result_id).first()
        if row is None:
            raise TaskResultDoesNotExist(result_id)
        return self._result(row)

    def _result(self, row, task=None):
        task = task or import_string(row.task_path)
        result = TaskResult(
            task=task, id=row.id, status=row.status, enqueued_at=row.enqueued_at, started_at=row.started_at,
            last_attempted_at=row.last_attempted_at, finished_at=row.finished_at, args=row.args, kwargs=row.kwargs,
            backend=self.alias, errors=[TaskError(**e) for e in row.errors or []], worker_ids=list(row.worker_ids))
        object.__setattr__(result, '_return_value', row.return_value)
        return result


def worker_id():
    return f'{socket.gethostname()[:40]}-{get_random_string(6)}'


def requeue_stale(now=None):
    """RUNNING tasks older than STALE_AFTER (their worker died): back to READY, or FAILED after MAX_ATTEMPTS."""
    from background.models import QueuedTask
    now = now or timezone.now()
    stale = QueuedTask.objects.filter(status=TaskResultStatus.RUNNING, last_attempted_at__lt=now - STALE_AFTER)
    count = 0
    for row in stale:
        row.status = TaskResultStatus.FAILED if len(row.worker_ids) >= MAX_ATTEMPTS else TaskResultStatus.READY
        row.errors = [*row.errors, {'exception_class_path': 'background.WorkerLost', 'traceback': 'The worker stopped.'}]
        row.save(update_fields=['status', 'errors'])
        count += 1
    return count


def claim(worker, queues=None):
    """The next READY task due now (highest priority, oldest first), marked RUNNING for this worker, or None."""
    from background.models import QueuedTask
    now = timezone.now()
    with transaction.atomic():
        qs = QueuedTask.objects.select_for_update(skip_locked=True).filter(status=TaskResultStatus.READY).exclude(
            run_after__gt=now)
        if queues:
            qs = qs.filter(queue_name__in=queues)
        row = qs.order_by('-priority', 'enqueued_at').first()
        if row is None:
            return None
        row.status = TaskResultStatus.RUNNING
        row.started_at = row.started_at or now
        row.last_attempted_at = now
        row.worker_ids = [*row.worker_ids, worker]
        row.save(update_fields=['status', 'started_at', 'last_attempted_at', 'worker_ids'])
    return row


def run(row, backend):
    """Run one claimed task and store the outcome (exceptions are recorded, never raised)."""
    result = backend._result(row)
    task_started.send(type(backend), task_result=result)
    try:
        task = result.task
        if task.takes_context:
            value = task.call(TaskContext(task_result=result), *row.args, **row.kwargs)
        else:
            value = task.call(*row.args, **row.kwargs)
        row.return_value = normalize_json(value)
        row.status = TaskResultStatus.SUCCESSFUL
    except KeyboardInterrupt:
        raise
    except BaseException as exc:
        logger.exception('Task %s (%s) failed', row.task_path, row.id)
        row.errors = [*row.errors, {'exception_class_path': f'{type(exc).__module__}.{type(exc).__qualname__}',
                                    'traceback': ''.join(format_exception(exc))[-20000:]}]
        row.status = TaskResultStatus.FAILED
    row.finished_at = timezone.now()
    row.save(update_fields=['return_value', 'status', 'errors', 'finished_at'])
    task_finished.send(type(backend), task_result=backend._result(row))
    return row.status
