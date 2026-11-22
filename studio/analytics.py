"""Cookie-free site analytics (batch 7 §6): page views, referring sites and Open in Attnd / store taps per site and
UTC day (`SiteHit`). One upsert per beacon; no IP, user agent or identifier is stored."""

import re
from datetime import timedelta

from django.db import connection
from django.db.models import Sum
from django.utils import timezone

from studio.models import SiteHit

_HOST_RE = re.compile(r'^[a-z0-9.-]{1,100}$')


def _clean_path(path):
    path = (path or '/').split('?', 1)[0].split('#', 1)[0][:120]
    return path if path.startswith('/') or not path else '/' + path


def _clean_host(host):
    host = (host or '').strip().lower().removeprefix('www.')[:100]
    return host if _HOST_RE.match(host) else ''


def count(site, kind, path, referrer):
    day = timezone.now().date()
    with connection.cursor() as cur:
        cur.execute(
            f'INSERT INTO {SiteHit._meta.db_table} (site_id, day, kind, path, referrer, count) '
            'VALUES (%s, %s, %s, %s, %s, 1) ON CONFLICT (site_id, day, kind, path, referrer) '
            f'DO UPDATE SET count = {SiteHit._meta.db_table}.count + 1',
            [site.pk, day, kind, _clean_path(path), _clean_host(referrer)])


def summary(site, days=30):
    """{views, open_app, get_app, per_day [{day, views}], pages [(path, n)], referrers [(host, n)]}."""
    since = timezone.now().date() - timedelta(days=days - 1)
    qs = SiteHit.objects.filter(site=site, day__gte=since)
    totals = dict(qs.values_list('kind').annotate(n=Sum('count')))
    per_day = dict(qs.filter(kind=SiteHit.VIEW).values_list('day').annotate(n=Sum('count')))
    days_list = [{'day': (since + timedelta(days=i)), 'views': per_day.get(since + timedelta(days=i), 0)}
                 for i in range(days)]
    peak = max([d['views'] for d in days_list] + [1])
    for d in days_list:
        d['pct'] = round(100 * d['views'] / peak)
    pages = list(qs.filter(kind=SiteHit.VIEW).values_list('path').annotate(n=Sum('count')).order_by('-n')[:8])
    referrers = list(qs.filter(kind=SiteHit.VIEW).exclude(referrer='').values_list('referrer')
                     .annotate(n=Sum('count')).order_by('-n')[:8])
    return {'days': days, 'views': totals.get(SiteHit.VIEW, 0), 'open_app': totals.get(SiteHit.OPEN_APP, 0),
            'get_app': totals.get(SiteHit.GET_APP, 0), 'per_day': days_list, 'pages': pages, 'referrers': referrers}
