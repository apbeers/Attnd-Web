"""Copy every outside image the platform shows into our own storage (attnd/images.py): locations' ``image_url``
(Google Places photo links are dropped and a new photo is looked for) and events' ``logo_url``.

    python manage.py self_host_images            # everything
    python manage.py self_host_images --dry-run  # list what would be copied
    python manage.py self_host_images --event CODE
"""
from django.core.management.base import BaseCommand

from attnd import images
from events.models import Event, Location


class Command(BaseCommand):
    help = 'Copy outside images (location image_url, event logo_url) into our own storage'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true')
        parser.add_argument('--event', help='One event code')

    def handle(self, *args, dry_run=False, event=None, **options):
        locations = Location.objects.exclude(image_url='').exclude(image_url__isnull=True).select_related('event')
        events = Event.objects.exclude(logo_url='')
        if event:
            locations = locations.filter(event__event_code__iexact=event)
            events = events.filter(event_code__iexact=event)
        counts = {}
        for loc in locations.order_by('pk'):
            if loc.image or images.is_ours(loc.image_url):
                continue
            kind = 'drop' if images.is_google_places_photo(loc.image_url) else 'copy'
            result = f'would {kind}' if dry_run else images.host_location(loc)
            counts[result] = counts.get(result, 0) + 1
            self.stdout.write(f'location {loc.pk} ({loc.event.event_code}): {result} {loc.image_url or ""}'[:200])
        for e in events.order_by('pk'):
            if images.is_ours(e.logo_url) or e.logo_source_url == e.logo_url:
                continue
            result = 'would copy' if dry_run else images.host_event_logo(e)
            counts[result] = counts.get(result, 0) + 1
            self.stdout.write(f'logo {e.event_code}: {result}')
        self.stdout.write(self.style.SUCCESS('Done: ' + (', '.join(f'{v} {k}' for k, v in counts.items()) or 'nothing to do')))
