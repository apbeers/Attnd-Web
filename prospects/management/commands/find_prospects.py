"""Run one prospect discovery now, inline: `python manage.py find_prospects` (it respects the daily spend cap and
never overlaps a running one). `--max N` lowers this run's cap on new events read."""

from django.core.management.base import BaseCommand

from prospects import discover
from prospects.models import ProspectRun


class Command(BaseCommand):
    help = 'Search the web for events that suit Attnd (prospects dashboard) and read their pages.'

    def add_arguments(self, parser):
        parser.add_argument('--max', type=int, default=None, help='New events to read at most (this run)')

    def handle(self, *args, **options):
        run = discover.execute(discover.create_run(ProspectRun.MANUAL).pk, max_new=options['max'])
        self.stdout.write(run.log or run.error or run.status)
        self.stdout.write(f'Run {run.pk}: {run.status}; {run.new} new, {run.updated} updated, '
                          f'{run.skipped} skipped; ${run.cost_usd:.4f}')
