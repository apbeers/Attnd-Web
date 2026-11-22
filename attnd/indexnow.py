"""IndexNow (batch 7, E): tell Bing, Yandex, Seznam and the other IndexNow engines when a public page changes, so
they recrawl it soon instead of on their own schedule. Production only (SITE_URL = https://attnd.app) and only with
INDEXNOW_KEY set.

Which pages: an event's join page when its content is published or it starts / stops being listed (published,
public, paid, not test / showcase: the same rule as the sitemap, `home.sitemaps.indexable_event`); a Studio site's
address when it is published, unpublished or taken down. Removals are sent too (IndexNow covers deleted pages).

Batching: changes only queue the URL (the database cache, so every dyno shares it); `flush()` (run_scheduled_tasks,
every 10 minutes) sends one POST per host to https://api.indexnow.org/indexnow with up to 10,000 URLs. The key is
served at `https://<host>/<key>.txt` on attnd.app and on the sites host (`key_file`). Counters per UTC day
(`usage()`) are on Diagnostics → Endpoints.
"""
import logging
import re
from datetime import datetime, timezone as dt_timezone
from urllib.parse import urlsplit

from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger('attnd.indexnow')

ENDPOINT = 'https://api.indexnow.org/indexnow'
QUEUE = 'indexnow:queue'
MAX_QUEUE = 10000
KEY_RE = re.compile(r'^[A-Za-z0-9-]{8,128}$')  # IndexNow: 8–128 of a–z, A–Z, 0–9, '-'


def key():
    value = (getattr(settings, 'INDEXNOW_KEY', '') or '').strip()
    return value if KEY_RE.match(value) else ''


def enabled():
    production = settings.SITE_URL.rstrip('/') == 'https://attnd.app'
    return bool(key()) and (production or getattr(settings, 'INDEXNOW_ANY_SITE', False))


def key_response(filename):
    """`/<key>.txt` → the key (both hosts), else None."""
    k = key()
    if k and enabled() and filename == f'{k}.txt':
        from django.http import HttpResponse
        return HttpResponse(k, content_type='text/plain; charset=utf-8')
    return None


def key_file(request, filename):
    """attnd.app's `/<key>.txt` (attnd/urls.py); the sites host answers it in studio.middleware."""
    from django.http import Http404
    response = key_response(filename)
    if response is None:
        raise Http404
    return response


def _today():
    return datetime.now(dt_timezone.utc).date().isoformat()


def _count(what, n=1):
    name = f'indexnow:n:{_today()}:{what}'
    try:
        cache.incr(name, n)
    except ValueError:
        cache.set(name, n, 3 * 86400)


def usage(day=None):
    day = day or _today()
    got = cache.get_many([f'indexnow:n:{day}:{w}' for w in ('queued', 'calls', 'urls', 'errors')])
    return {w: got.get(f'indexnow:n:{day}:{w}', 0) for w in ('queued', 'calls', 'urls', 'errors')}


def queue(*urls):
    urls = [u for u in urls if u and u.startswith('https://')]
    if not urls or not enabled():
        return 0
    pending = cache.get(QUEUE) or []
    added = [u for u in dict.fromkeys(urls) if u not in pending]
    if added:
        cache.set(QUEUE, (pending + added)[-MAX_QUEUE:], 7 * 86400)
        _count('queued', len(added))
    return len(added)


def event_url(event):
    from django.urls import reverse
    return settings.SITE_URL.rstrip('/') + reverse('join:join_event', args=[event.event_code])


def event_changed(event, before=None):
    """After a save: the join page when it is (or was) meant to be found."""
    from events.models import Event
    from home.sitemaps import indexable_event
    was = bool(before) and (before.get('status') == Event.STATUS_PUBLISHED and before.get('public')
                            and before.get('paid') and not event.testing_only and not event.showcase)
    if indexable_event(event) or was:
        queue(event_url(event))


def site_changed(site):
    from studio import hosting
    queue(hosting.site_url(site))


def flush():
    """Send what's queued, one POST per host. Returns the number of URLs sent (failures stay queued once)."""
    import httpx
    if not enabled():
        return 0
    pending = cache.get(QUEUE) or []
    if not pending:
        return 0
    cache.delete(QUEUE)
    by_host = {}
    for url in pending:
        by_host.setdefault(urlsplit(url).netloc, []).append(url)
    sent, retry = 0, []
    for host, urls in by_host.items():
        for start in range(0, len(urls), 10000):
            batch = urls[start:start + 10000]
            _count('calls')
            try:
                resp = httpx.post(ENDPOINT, timeout=15, json={
                    'host': host, 'key': key(), 'keyLocation': f'https://{host}/{key()}.txt', 'urlList': batch},
                    headers={'Content-Type': 'application/json; charset=utf-8', 'User-Agent': 'Attnd IndexNow'})
                ok = resp.status_code in (200, 202)
            except Exception as exc:
                logger.warning('IndexNow %s failed: %s', host, exc.__class__.__name__)
                ok, resp = False, None
            if ok:
                sent += len(batch)
                _count('urls', len(batch))
            else:
                _count('errors')
                logger.warning('IndexNow %s answered %s', host, getattr(resp, 'status_code', 'no answer'))
                if getattr(resp, 'status_code', 0) in (0, 429, 500, 502, 503):
                    retry += batch  # try once more next run; 400 / 403 / 422 are ours to fix
    if retry and not cache.get('indexnow:retried'):
        cache.set('indexnow:retried', True, 3600)
        queue(*retry)
    return sent
