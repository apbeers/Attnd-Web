"""Look for place images (stage D) for existing events: places without an image, and places whose image is a Google
Places photo link from an older import (those expire and may not be stored). Stops at the daily cost cap."""

from django.core.management.base import BaseCommand

from events import place_images
from events.models import Event


class Command(BaseCommand):
    help = 'Find photos for places without one (and replace Google Places photo links), within the daily cap.'

    def add_arguments(self, parser):
        parser.add_argument('--event', help='Only this event code')
        parser.add_argument('--limit', type=int, default=None, help='At most this many places per event')

    def handle(self, *args, event=None, limit=None, **options):
        if not place_images.enabled():
            self.stdout.write('Place images are off (no ANTHROPIC_API_KEY or PLACE_IMAGES_ENABLED=0).')
            return
        events = Event.objects.exclude(status=Event.STATUS_DRAFT) if not event else \
            Event.objects.filter(event_code__iexact=event)
        for ev in events.order_by('pk'):
            if place_images.budget_left() <= 0:
                self.stdout.write('Daily cap reached; run again tomorrow.')
                break
            if not any(place_images.wanted(loc) for loc in ev.location_set.all()):
                continue
            counts = place_images.find_for_event(ev, limit=limit)
            self.stdout.write(f'{ev.event_code}: ' + ', '.join(f'{k} {v}' for k, v in sorted(counts.items())))
