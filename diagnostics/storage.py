"""How much room diagnostics take (the 250 MB cap, API-013).

`used_bytes()` asks Postgres for the allocated size of the diagnostics tables with their indexes and TOAST
(`pg_total_relation_size`, a catalogue lookup: cheap) and caches it for a minute, so uploads can check it on
every request. Rows deleted by the purge leave free space that new rows reuse; the purge runs VACUUM FULL
after trimming for the cap, so the number drops again.
"""
from django.conf import settings
from django.core.cache import cache
from django.db import connection

TABLES = ('diagnostics_diagnosticentry', 'diagnostics_diagnosticsession', 'diagnostics_diagnosticreport')
CACHE_KEY = 'diagnostics:used-bytes'


def measure():
    with connection.cursor() as cursor:
        cursor.execute(
            'SELECT COALESCE(SUM(pg_total_relation_size(to_regclass(t))), 0) FROM unnest(%s::text[]) AS t',
            [list(TABLES)])
        return int(cursor.fetchone()[0])


def used_bytes(fresh=False):
    if not fresh:
        cached = cache.get(CACHE_KEY)
        if cached is not None:
            return cached
    value = measure()
    cache.set(CACHE_KEY, value, 60)
    return value


def cap_bytes():
    return settings.DIAGNOSTICS_MAX_BYTES


def over_cap():
    return used_bytes() >= cap_bytes()


def forget():
    cache.delete(CACHE_KEY)
