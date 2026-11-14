"""Worker loop for AI jobs. Run as a Heroku worker dyno (set INGEST_RUN_INLINE=0 on web when you do)."""

import time

from django.core.management.base import BaseCommand
from django.db import close_old_connections

from scrape import jobs


class Command(BaseCommand):
    help = 'Process queued AI import/refresh jobs.'

    def add_arguments(self, parser):
        parser.add_argument('--once', action='store_true', help='Process the queue once and exit')

    def handle(self, *args, once=False, **options):
        while True:
            close_old_connections()
            job = jobs.claim_next()
            if job is not None:
                self.stdout.write(f'Running job {job.pk} ({job.kind})')
                jobs.run_job(job.pk, already_claimed=True)
                continue
            if once:
                return
            time.sleep(3)
