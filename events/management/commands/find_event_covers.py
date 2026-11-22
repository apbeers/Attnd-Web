"""Backfill the home page gallery's card covers (events/covers.py): published prospect test events and showcase events
without a flyer or place photo get a licensed Wikimedia Commons photo near the venue, else an AI picture.

    python manage.py find_event_covers                  # every gallery event without a cover
    python manage.py find_event_covers --event CHILIFW  # one event
    python manage.py find_event_covers --no-ai          # Wikimedia only
    python manage.py find_event_covers --force          # look again even where a cover exists
"""
from django.core.management.base import BaseCommand
from django.db.models import Q

from events import covers
from events.models import Event


class Command(BaseCommand):
    help = 'Find covers for the home page gallery cards (Wikimedia Commons, else AI)'

    def add_arguments(self, parser):
        parser.add_argument('--event', help='One event code')
        parser.add_argument('--no-ai', action='store_true', help='Never generate an AI picture')
        parser.add_argument('--force', action='store_true', help='Replace existing covers too')

    def handle(self, *args, event=None, no_ai=False, force=False, **options):
        rows = Event.objects.filter(status=Event.STATUS_PUBLISHED)
        if event:
            rows = rows.filter(event_code__iexact=event)
        else:
            rows = rows.filter(Q(testing_only=True, prospects__isnull=False) | Q(showcase=True)).distinct()
        counts = {}
        for e in rows.order_by('pk'):
            if covers.budget_left() <= 0:
                self.stdout.write('Daily cover budget used up; stopping.')
                break
            result = covers.ensure(e, ai=not no_ai, force=force)
            counts[result] = counts.get(result, 0) + 1
            self.stdout.write(f'{e.event_code}: {result}{" · " + e.cover_credit if e.cover else ""}')
        self.stdout.write(self.style.SUCCESS(
            'Done: ' + (', '.join(f'{v} {k}' for k, v in counts.items()) or 'nothing to do')
            + f' (${covers.spent_today():.4f} spent today)'))
