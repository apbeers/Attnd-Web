"""Which API versions and app versions are still in use.

Run before retiring an old API version or dropping support for old app builds (see API_CHANGES.md):

    heroku run -a attnd-web python manage.py api_usage
    heroku run -a attnd-web python manage.py api_usage --days 30
"""
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db.models import Count
from django.utils import timezone

from events.models import Device


class Command(BaseCommand):
    help = 'Count devices seen recently by API version, platform and app version.'

    def add_arguments(self, parser):
        parser.add_argument('--days', type=int, default=90, help='Only count devices seen in the last N days (default 90).')

    def handle(self, *args, days, **options):
        since = timezone.now() - timedelta(days=days)
        recent = Device.objects.real().filter(last_seen__gte=since)
        self.stdout.write(f'Devices seen in the last {days} days: {recent.count()}\n')

        self.stdout.write('By API version:')
        for row in recent.values('api_version').annotate(n=Count('id')).order_by('api_version'):
            label = f"v{row['api_version']}" if row['api_version'] else 'unknown (seen before tracking began)'
            self.stdout.write(f"  {label:<40} {row['n']}")

        self.stdout.write('\nBy platform and app version:')
        rows = recent.values('api_version', 'platform', 'app_version').annotate(n=Count('id'))
        for row in sorted(rows, key=lambda r: (r['platform'], r['api_version'] or 0, r['app_version'])):
            api = f"v{row['api_version']}" if row['api_version'] else '?'
            self.stdout.write(f"  {row['platform'] or '?':<8} API {api:<4} app {row['app_version'] or '(not sent)':<14} {row['n']}")

        never = Device.objects.filter(last_seen__isnull=True).count()
        if never:
            self.stdout.write(f'\n{never} older device records have never been seen since tracking began.')
