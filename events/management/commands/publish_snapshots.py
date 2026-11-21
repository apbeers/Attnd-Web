"""§4: give every event attendees can reach its first published copy (release phase; idempotent)."""
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Snapshot the published copy of every published or archived event that has none yet.'

    def handle(self, *args, **options):
        from events import drafts
        from events.models import Event
        made = 0
        for event in Event.objects.exclude(status=Event.STATUS_DRAFT).filter(published__isnull=True).iterator():
            try:
                drafts.publish(event, push=False)
                made += 1
            except Exception as exc:  # one bad event must not stop the release
                self.stderr.write(f'{event.pk}: {exc}')
        self.stdout.write(f'Published copies made: {made}')
