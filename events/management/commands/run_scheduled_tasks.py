"""
Periodic housekeeping. Add to Heroku Scheduler "every 10 minutes":

    python manage.py run_scheduled_tasks
"""

from django.core.management import call_command
from django.core.management.base import BaseCommand

from events import push
from scrape import jobs


class Command(BaseCommand):
    help = 'Send due notifications, start scheduled AI refreshes, recover stuck jobs, expire trials.'

    def handle(self, *args, **options):
        sent = push.send_due_notifications()
        recovered = jobs.recover_stale()
        queued = jobs.queue_scheduled_refreshes()
        self.stdout.write(f'notifications sent: {sent}; stale jobs recovered: {recovered}; refreshes queued: {queued}')

        # Idempotent and cheap: delete unclaimed trial events past their expiry
        call_command('expire_trial_events', stdout=self.stdout)

        # Inline jobs run in daemon threads; give them a moment to finish in this one-off dyno.
        import threading
        import time
        deadline = time.monotonic() + 240
        while time.monotonic() < deadline and any(t.name.startswith('ingest-') for t in threading.enumerate()):
            time.sleep(2)
