"""
Periodic housekeeping. Add to Heroku Scheduler "every 10 minutes":

    python manage.py run_scheduled_tasks
"""

from django.core.management import call_command
from django.core.management.base import BaseCommand

from events import push
from scrape import jobs, watch


class Command(BaseCommand):
    help = 'Send due notifications, check watched event websites, recover stuck jobs, expire trials.'

    def handle(self, *args, **options):
        sent = push.send_due_notifications()
        recovered = jobs.recover_stale()
        watched, queued = watch.check_watched_events()
        from attestation.views import purge_expired_nonces
        purge_expired_nonces()
        # Privacy policy: device records unused for 12 months are deleted
        from datetime import timedelta
        from django.utils import timezone
        from events.models import Device
        Device.objects.filter(last_seen__lt=timezone.now() - timedelta(days=365)).delete()
        self.stdout.write(f'notifications sent: {sent}; stale jobs recovered: {recovered}; '
                          f'websites checked: {watched}; AI updates queued: {queued}')

        # Idempotent and cheap: delete unclaimed trial events past their expiry
        call_command('expire_trial_events', stdout=self.stdout)

        # Inline jobs run in daemon threads; give them a moment to finish in this one-off dyno.
        import threading
        import time
        deadline = time.monotonic() + 240
        while time.monotonic() < deadline and any(t.name.startswith('ingest-') for t in threading.enumerate()):
            time.sleep(2)
