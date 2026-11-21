"""After SITES_HOST changes (e.g. herokuapp.com → attnd.site): the address of a published Studio site is never
stored (website_url is built from SITES_HOST on every bundle), but the apps keep their cached bundle until its
content_version changes. This bumps the content version of every event with a published site, once, so the apps
fetch the new website_url (API-016) at their next refresh.

    python manage.py refresh_site_addresses [--dry-run]
"""
from django.core.management.base import BaseCommand

from studio.models import Site


class Command(BaseCommand):
    help = "Bump the content version of events with a published Studio site (after SITES_HOST changed)"

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, dry_run=False, **options):
        sites = Site.objects.filter(published__isnull=False, suspended=False).select_related('event')
        n = 0
        for site in sites:
            self.stdout.write(f'{site.event.event_code}: {site.public_url()}')
            if not dry_run:
                site.event.bump_version()
            n += 1
        self.stdout.write(self.style.SUCCESS(f'{"Would bump" if dry_run else "Bumped"} {n} event(s).'))
