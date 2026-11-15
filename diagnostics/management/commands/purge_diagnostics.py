"""Delete diagnostics past their retention and keep them under their size cap (diagnostics.purge).

run_scheduled_tasks runs the same purge; run this by hand to see what it does:

    python manage.py purge_diagnostics
"""
from django.core.management.base import BaseCommand

from diagnostics import storage
from diagnostics.purge import purge


class Command(BaseCommand):
    help = 'Delete diagnostics past retention (dev 7 d, debug 14 d, reports 30 d) and enforce the 250 MB cap.'

    def handle(self, *args, **options):
        result = purge()
        used = storage.used_bytes(fresh=True)
        self.stdout.write('diagnostics purge: ' + ', '.join(f'{k} {v}' for k, v in result.items())
                          + f'; now {used / 1024 / 1024:.1f} of {storage.cap_bytes() / 1024 / 1024:.0f} MB')
