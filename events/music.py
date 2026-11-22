"""API-030: "Listen on Apple Music" links for performers (owner, Nov 21, 2026: Apple Music only).

For every performer in a schedule item's context (API-018 `performers`) the server looks the artist up once in the
Apple Music catalog (storefront `us`) and keeps only a confident match: the normalised names are equal, the genres
overlap when both sides have some, and several equal names are only accepted when the genres single one out (Apple's
search order then breaks the tie). The result lives in `ScheduledItem.music` keyed by the normalised performer name:

    {"prairie lights": {"url": "https://music.apple.com/us/artist/…/123", "id": "123", "at": "2026-11-22T…"},
     "dj nobody": {"url": "", "at": "…"},                       # looked up, nothing confident
     "the band": {"url": "https://music.apple.com/…", "locked": true}}  # set (or cleared) by the organizer

A name is looked up again only when it changes (a new key); organizer-set entries (`locked`) are never touched. The
bundle adds `apple_music_url` to each performer with a link (`events.content`). The developer token is an ES256 JWT
signed with the MusicKit key (`APPLE_MUSIC_KEY_ID`, `APPLE_MUSIC_PRIVATE_KEY`, team `APPLE_TEAM_ID`); without them
nothing is looked up. Lookups are cached (found 30 days, nothing 7 days), counted per UTC day like the Google
counters (Diagnostics → Endpoints), and a 429 pauses them for an hour.
"""

import hashlib
import json
import logging
import re
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone as dt_timezone

from django.conf import settings
from django.core.cache import cache
from django.db import close_old_connections, connection, transaction

logger = logging.getLogger('attnd.music')

API = 'https://api.music.apple.com/v1/catalog/{storefront}/search'
STOREFRONT = 'us'
TIMEOUT = 8
FOUND_TTL = 30 * 24 * 3600
NONE_TTL = 7 * 24 * 3600
PAUSE_KEY = 'music:pause'
MAX_LOOKUPS_PER_RUN = 60
URL_RE = re.compile(r'^https://(?:music|itunes)\.apple\.com/[^\s]+$')


def enabled():
    return bool(getattr(settings, 'APPLE_MUSIC_KEY_ID', '') and getattr(settings, 'APPLE_MUSIC_PRIVATE_KEY', '')
                and getattr(settings, 'APPLE_TEAM_ID', ''))


def valid_url(url):
    return bool(url) and bool(URL_RE.match(str(url).strip()))


# ---------------------------------------------------------------------------
# Names
# ---------------------------------------------------------------------------

def norm(name):
    """Case, accents, punctuation and a leading "the" don't matter; "&" = "and"."""
    text = unicodedata.normalize('NFKD', str(name or '')).encode('ascii', 'ignore').decode().casefold()
    text = text.replace('&', ' and ')
    text = re.sub(r'[^a-z0-9]+', ' ', text).strip()
    return re.sub(r'^the ', '', text)


def _genre_words(genres):
    words = set()
    for g in genres or []:
        words |= set(norm(g).split()) - {'music', 'and'}
    return words


def match(name, genres, candidates):
    """The confident candidate {id, url, name} for a performer, or None. `candidates`: Apple's artists in search
    order, each {'id', 'attributes': {'name', 'url', 'genreNames'}}."""
    wanted = norm(name)
    if not wanted:
        return None
    same = [c for c in candidates or [] if norm((c.get('attributes') or {}).get('name')) == wanted
            and valid_url((c.get('attributes') or {}).get('url'))]
    if not same:
        return None
    mine = _genre_words(genres)
    if mine:
        known = [c for c in same if (c.get('attributes') or {}).get('genreNames')]
        overlap = [c for c in known if mine & _genre_words(c['attributes']['genreNames'])]
        if known and not overlap and len(known) == len(same):
            return None  # every same-named artist plays something else
        if overlap:
            same = overlap
        elif len(same) > 1:
            return None
    elif len(same) > 1:
        return None  # several artists of that name and nothing to tell them apart
    best = same[0]
    return {'id': str(best.get('id') or ''), 'url': best['attributes']['url'], 'name': best['attributes']['name']}


# ---------------------------------------------------------------------------
# Apple Music API
# ---------------------------------------------------------------------------

_token = {'value': '', 'exp': 0}
_token_lock = threading.Lock()


def developer_token():
    """An ES256 developer token (12 h), reused until an hour before it expires."""
    import jwt
    now = int(time.time())
    with _token_lock:
        if _token['value'] and _token['exp'] - 3600 > now:
            return _token['value']
        key = settings.APPLE_MUSIC_PRIVATE_KEY.replace('\\n', '\n')
        value = jwt.encode({'iss': settings.APPLE_TEAM_ID, 'iat': now, 'exp': now + 12 * 3600}, key,
                           algorithm='ES256', headers={'kid': settings.APPLE_MUSIC_KEY_ID})
        _token.update(value=value, exp=now + 12 * 3600)
        return value


def _today():
    return datetime.now(dt_timezone.utc).date().isoformat()


COUNTERS = ('calls', 'hits', 'found', 'none', 'errors', 'paused')


def _count(what, n=1):
    key = f'music:n:{_today()}:{what}'
    try:
        cache.incr(key, n)
    except ValueError:
        cache.set(key, n, 3 * 24 * 3600)


def usage(day=None):
    """{calls, hits, found, none, errors, paused} for one UTC day (default today)."""
    day = day or _today()
    keys = {f'music:n:{day}:{w}': w for w in COUNTERS}
    got = cache.get_many(list(keys))
    return {w: got.get(k, 0) for k, w in keys.items()}


def _search(term):
    query = urllib.parse.urlencode({'term': term, 'types': 'artists', 'limit': 10})
    request = urllib.request.Request(API.format(storefront=STOREFRONT) + '?' + query,
                                     headers={'Authorization': f'Bearer {developer_token()}',
                                              'User-Agent': 'Attnd/1.0 (+https://attnd.app)'})
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        data = json.loads(response.read().decode())
    return ((data.get('results') or {}).get('artists') or {}).get('data') or []


def paused():
    return bool(cache.get(PAUSE_KEY))


class Paused(Exception):
    pass


def lookup(name, genres=None):
    """{id, url, name} or None (nothing confident). Raises Paused while Apple asked us to slow down, and lets other
    errors through (nothing is remembered for them)."""
    key = 'music:am:' + hashlib.sha256(f'{norm(name)}|{",".join(sorted(_genre_words(genres)))}'.encode()).hexdigest()
    cached = cache.get(key)
    if cached is not None:
        _count('hits')
        return cached or None
    if cache.get(PAUSE_KEY):
        _count('paused')
        raise Paused
    _count('calls')
    try:
        candidates = _search(name)
    except urllib.error.HTTPError as exc:
        _count('errors')
        if exc.code == 429:
            cache.set(PAUSE_KEY, 1, 3600)
            logger.warning('Apple Music asked us to slow down (429): lookups paused for an hour')
            raise Paused from exc
        logger.warning('Apple Music search failed: HTTP %s', exc.code)
        raise
    except Exception:
        _count('errors')
        logger.warning('Apple Music search failed', exc_info=True)
        raise
    found = match(name, genres, candidates)
    _count('found' if found else 'none')
    cache.set(key, found or {}, FOUND_TTL if found else NONE_TTL)
    logger.info('Apple Music lookup: %s', 'found' if found else 'nothing confident')
    return found


# ---------------------------------------------------------------------------
# Schedule items
# ---------------------------------------------------------------------------

def performers(item):
    return [p for p in ((item.context or {}).get('performers') or []) if isinstance(p, dict) and p.get('name')]


def wanted(item):
    """Names of this item's performers that haven't been looked up (or set by hand) yet."""
    have = item.music or {}
    return [p for p in performers(item) if norm(p['name']) and norm(p['name']) not in have]


def links(item):
    """{normalised name: url} the bundle sends (confident matches and organizer links)."""
    return {k: v['url'] for k, v in (item.music or {}).items() if isinstance(v, dict) and valid_url(v.get('url'))}


def with_links(context, item):
    """The bundle's item context with `apple_music_url` on each performer that has a link (API-030; additive)."""
    found = links(item)
    if not context or not found:
        return context
    for performer in context.get('performers') or []:
        url = found.get(norm(performer.get('name')))
        if url:
            performer['apple_music_url'] = url
    return context


def refresh_item(item, force=False, budget=None):
    """Look up the performers that need it (force: every one not set by hand). Returns (looked_up, found). Saves
    the item's `music` (dropping names that are gone) and bumps the content version once when a link changed."""
    if not enabled():
        return 0, 0
    budget = [MAX_LOOKUPS_PER_RUN] if budget is None else budget
    music = {k: v for k, v in (item.music or {}).items() if isinstance(v, dict)}
    names = {norm(p['name']): p for p in performers(item) if norm(p['name'])}
    before_links = links(item)
    music = {k: v for k, v in music.items() if k in names}  # performers removed: their entries go
    looked = found = 0
    for key, performer in names.items():
        entry = music.get(key)
        if entry and (entry.get('locked') or not force):
            continue
        if budget[0] <= 0:
            break
        try:
            result = lookup(performer['name'], performer.get('genres'))
        except Paused:
            break
        except Exception:
            continue  # nothing remembered: tried again next time
        budget[0] -= 1
        looked += 1
        now = datetime.now(dt_timezone.utc).isoformat(timespec='seconds')
        if result:
            found += 1
            music[key] = {'url': result['url'], 'id': result['id'], 'at': now}
        else:
            music[key] = {'url': '', 'at': now}
    if music != (item.music or {}):
        from events.content import maybe_bump
        from events.models import ScheduledItem
        ScheduledItem.objects.filter(pk=item.pk).update(music=music)
        item.music = music
        if links(item) != before_links:
            maybe_bump(item.event_id)
    return looked, found


def refresh_event(event, force=False):
    """Every schedule item of the event. Returns {'looked_up', 'found', 'with_links', 'performers'}."""
    budget = [MAX_LOOKUPS_PER_RUN]
    looked = found = 0
    items = list(event.scheduleditem_set.all())
    for item in items:
        a, b = refresh_item(item, force=force, budget=budget)
        looked, found = looked + a, found + b
    return {'looked_up': looked, 'found': found,
            'with_links': sum(len(links(i)) for i in items), 'performers': sum(len(performers(i)) for i in items)}


def set_links(item, edits):
    """The organizer's own links from the console: {performer name: url or ''} → locked entries ('' = no link,
    never looked up again). An unchanged automatic link stays automatic."""
    music = dict(item.music or {})
    for name, url in edits.items():
        key = norm(name)
        if not key:
            continue
        url = (url or '').strip()
        current = music.get(key) or {}
        if (url and url == current.get('url')) or (not url and current.get('locked') and not current.get('url')):
            continue  # unchanged: an automatic link stays automatic
        music[key] = {'url': url if valid_url(url) else '', 'locked': True,
                      'at': datetime.now(dt_timezone.utc).isoformat(timespec='seconds')}
    item.music = music


# ---------------------------------------------------------------------------
# Background
# ---------------------------------------------------------------------------

_queue = None
_queue_lock = threading.Lock()


def _worker(q):
    while True:
        item_id = q.get()
        close_old_connections()
        try:
            from events.models import ScheduledItem
            item = ScheduledItem.objects.filter(pk=item_id).first()
            if item is not None:
                refresh_item(item)
        except Exception:
            logger.exception('Apple Music links for item %s failed', item_id)
        finally:
            connection.close()
            q.task_done()


def schedule(item_id):
    """After a schedule item's performers change: look them up once the save commits, in one background thread."""
    if not (enabled() and getattr(settings, 'MUSIC_LINKS_ON_SAVE', True)):
        return

    def put():
        global _queue
        import queue
        with _queue_lock:
            if _queue is None:
                _queue = queue.Queue()
                threading.Thread(target=_worker, args=(_queue,), daemon=True, name='music-links').start()
        _queue.put(item_id)
    transaction.on_commit(put)
