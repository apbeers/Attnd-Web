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
        from studio.agent import recover_stale as recover_studio_runs
        recover_studio_runs()  # Studio AI runs interrupted by a restart
        from agent.loop import recover_stale as recover_agent_runs
        recover_agent_runs()  # Ask Attnd runs interrupted by a restart; unanswered confirmations after a day
        watched, queued = watch.check_watched_events()
        from attestation.views import purge_expired_nonces
        purge_expired_nonces()
        from console.undo import purge as purge_undo
        purge_undo()  # Undo entries a day past their 24 h
        from console.user_sessions import purge as purge_sessions
        purge_sessions()  # signed-in sessions that have ended (Account → Signed in on other devices)
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

        # Prospects (staff): a discovery run when its schedule is on and due (inline; never overlaps another run).
        try:
            from prospects.schedule import maybe_run
            run = maybe_run()
            if run is not None:
                self.stdout.write(f'prospects run {run.pk}: {run.status}, {run.new} new, ${run.cost_usd:.4f}')
        except Exception:
            import logging
            logging.getLogger(__name__).exception('Prospect discovery failed')

        # API-021: "Plan your day" suggestions of events whose content changed (Claude Haiku 5.5; skipped without a key).
        try:
            from events.plan_suggestions import refresh_due
            refreshed = refresh_due()
            if refreshed:
                self.stdout.write(f'plan suggestions refreshed: {refreshed}')
        except Exception:
            import logging
            logging.getLogger(__name__).exception('Plan suggestions failed')

        # F5: with the database task queue and no `tasks` dyno, drain what is due (≤ 4 minutes, before the next run).
        import background
        if background.database_queue():
            call_command('run_tasks', once=True, max_seconds=240, stdout=self.stdout)

        # Inline jobs run in daemon threads; give them a moment to finish in this one-off dyno.
        import threading
        import time
        deadline = time.monotonic() + 240
        while time.monotonic() < deadline and any(t.name.startswith('ingest-') for t in threading.enumerate()):
            time.sleep(2)
