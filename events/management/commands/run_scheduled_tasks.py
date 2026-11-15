"""
Periodic housekeeping. Add to Heroku Scheduler "every 10 minutes":

    python manage.py run_scheduled_tasks
"""

from django.core.management import call_command
from django.core.management.base import BaseCommand

from events import push
from scrape import jobs, watch


def keep_demo_current(stdout=None):
    """Reload the demo event (same id, same owner) when it is about to end or has ended."""
    from datetime import timedelta

    from django.conf import settings
    from django.utils import timezone

    from events.models import Event
    code = getattr(settings, 'DEMO_EVENT_CODE', '')
    event = Event.objects.filter(event_code=code.upper()).select_related('user').first() if code else None
    if event is None:
        return False
    if event.timing == 'now' and event.effective_end - timezone.now() > timedelta(hours=12):
        return False
    args = ['load_demo_event', '--code', code]
    if event.user_id:
        args += ['--owner', event.user.email]
    call_command(*args, stdout=stdout)
    return True


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

        # Diagnostics (API-013): retention (dev 7 d, debug 14 d, reports 30 d) and the 250 MB cap. Cheap when
        # there is nothing to delete, so it runs every time (which also covers "nightly").
        from diagnostics.purge import purge as purge_diagnostics
        try:
            purged = purge_diagnostics()
            self.stdout.write('diagnostics purged: ' + ', '.join(f'{k} {v}' for k, v in purged.items() if v))
        except Exception:  # never stop the other housekeeping
            import logging
            logging.getLogger(__name__).exception('Diagnostics purge failed')

        # Idempotent and cheap: delete unclaimed trial events past their expiry
        call_command('expire_trial_events', stdout=self.stdout)

        # The demo event (store reviewers and sales demos use it) must always be happening now.
        keep_demo_current(self.stdout)

        # Inline jobs run in daemon threads; give them a moment to finish in this one-off dyno.
        import threading
        import time
        deadline = time.monotonic() + 240
        while time.monotonic() < deadline and any(t.name.startswith('ingest-') for t in threading.enumerate()):
            time.sleep(2)
