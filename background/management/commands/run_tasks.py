"""Run queued django.tasks (stage F5, `background.backend.DatabaseBackend`).

    python manage.py run_tasks                  # a worker dyno: forever
    python manage.py run_tasks --once --max-seconds 480   # Heroku Scheduler: drain what is due, then stop
"""

import signal
import time

from django.core.management.base import BaseCommand
from django.db import close_old_connections, connection
from django.tasks import task_backends

from background.backend import DatabaseBackend, claim, requeue_stale, run, worker_id


class Command(BaseCommand):
    help = 'Run queued background tasks (the database task backend).'

    def add_arguments(self, parser):
        parser.add_argument('--once', action='store_true', help='Stop when nothing is due')
        parser.add_argument('--max-seconds', type=int, default=0, help='Stop after this long (0 = no limit)')
        parser.add_argument('--queue', action='append', help='Only these queues')
        parser.add_argument('--sleep', type=float, default=2.0, help='Seconds between empty polls')

    def handle(self, *args, once=False, max_seconds=0, queue=None, sleep=2.0, **options):
        backend = task_backends['default']
        if not isinstance(backend, DatabaseBackend):
            self.stdout.write('TASKS uses another backend; nothing to run.')
            return
        stopping = []
        signal.signal(signal.SIGTERM, lambda *_: stopping.append(True))  # Heroku: finish the current task, then stop
        me = worker_id()
        deadline = time.monotonic() + max_seconds if max_seconds else None
        done = requeued = 0
        requeued = requeue_stale()
        while not stopping and (deadline is None or time.monotonic() < deadline):
            if not connection.in_atomic_block:
                close_old_connections()  # a broken or too-old connection is replaced before each task
            row = claim(me, queue)
            if row is None:
                if once:
                    break
                if not connection.in_atomic_block:
                    connection.close()
                time.sleep(sleep)
                continue
            run(row, backend)
            done += 1
        self.stdout.write(f'Ran {done} task(s); {requeued} stale task(s) requeued.')
