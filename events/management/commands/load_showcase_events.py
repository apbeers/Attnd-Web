"""Create or refresh the home page's showcase events (events/showcase.py), happening now.

    python manage.py load_showcase_events                 # all eight, in place (same codes and ids)
    python manage.py load_showcase_events --if-enabled    # the release phase: only where SHOWCASE_EVENTS is on (dev)
    python manage.py load_showcase_events --images        # also an AI cover for each event without one (OpenAI)
"""

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand

from events import showcase


class Command(BaseCommand):
    help = 'Create or refresh the showcase events of the home page gallery (idempotent).'

    def add_arguments(self, parser):
        parser.add_argument('--if-enabled', action='store_true', help='Do nothing unless SHOWCASE_EVENTS is on')
        parser.add_argument('--images', action='store_true', help='Generate a cover image for each (costs a few cents)')
        parser.add_argument('--owner', help='Email of the account that owns them (default: unassigned)')

    def handle(self, *args, if_enabled=False, images=False, owner=None, **options):
        if if_enabled and not showcase.enabled():
            self.stdout.write('Showcase events are off here (SHOWCASE_EVENTS).')
            return
        user = User.objects.filter(email__iexact=owner).first() if owner else None
        events = showcase.load_all(owner=user)
        self.stdout.write(f'Showcase events: {", ".join(e.event_code for e in events)}.')
        if images:
            try:
                made = showcase.generate_covers(events)
                self.stdout.write(f'Covers made: {made}.')
            except Exception as exc:  # the gallery draws the theme without a cover
                self.stderr.write(f'Covers skipped: {exc}')
