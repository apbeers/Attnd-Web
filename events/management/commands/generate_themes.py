"""API-023 backfill: give events without a colour theme one (Claude Haiku from their website's colours, logo and style;
the fallback without the AI).

    python manage.py generate_themes                 # every event without a theme
    python manage.py generate_themes --event DEMO    # one event (replaces its theme unless a person edited it)
    python manage.py generate_themes --force         # also replace AI-made themes (never ones a person edited)
"""

from django.core.management.base import BaseCommand

from events import theme as T
from events import theme_ai
from events.models import Event


class Command(BaseCommand):
    help = 'Give events without a colour theme one (API-023).'

    def add_arguments(self, parser):
        parser.add_argument('--event', help='Only this event code')
        parser.add_argument('--force', action='store_true', help='Also replace themes the AI made earlier')
        parser.add_argument('--limit', type=int, default=200)

    def handle(self, *args, event=None, force=False, limit=200, **options):
        qs = Event.objects.exclude(status=Event.STATUS_DRAFT) if not event else Event.objects.filter(
            event_code__iexact=event)
        done = 0
        for e in qs.order_by('-start_time')[:limit]:
            if 'theme' in (e.locked_fields or []):
                self.stdout.write(f'{e.event_code}: kept (edited by a person)')
                continue
            if T.normalize(e.theme) and not (force or event):
                continue
            theme = theme_ai.apply(e, force=True)
            done += 1
            self.stdout.write(f'{e.event_code}: {theme.get("source")} {theme.get("light", {}).get("primary", "")}')
        self.stdout.write(self.style.SUCCESS(f'{done} event{"s" if done != 1 else ""} themed.'))
