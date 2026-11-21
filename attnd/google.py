"""Every server-side Google Maps Platform call goes through here (owner, Nov 21, 2026: cut Places usage; daily quotas
in Google Cloud, SETUP.md).

- ``text_search(query, near=None, radius=…, limit=1, website=False)``: Places API (New) Text Search with a field mask.
- ``place_website(place_id)``: Place Details (``websiteUri`` only) for a place id we already have.
- ``geocode(address)``: the Geocoding API.

Each call: the server key (``GOOGLE_PLACES_API_KEY``), an 8 s timeout, one INFO log line, a per-day counter of calls,
cache hits, errors and quota refusals per API (``usage()``; shown in the staff Diagnostics panel), and quota
handling: a 429 / ``RESOURCE_EXHAUSTED`` / ``OVER_QUERY_LIMIT`` is logged once and that API answers "nothing" without
calling Google until midnight Pacific (when Google's daily quotas reset); no retries.

Caching follows the Google Maps Platform Service Specific Terms and the Places / Geocoding policies (checked Nov 21,
2026): place IDs may be kept indefinitely; latitude / longitude may be cached for up to 30 consecutive calendar days;
other Places content (names, formatted addresses, websites) is not stored long-term, so it is cached for a day only.
"Nothing found" answers are ours, cached 30 days. Keys are a hash of the normalised query and the bias area rounded to
0.01° (about 1 km), in the database cache shared by every dyno.
"""

import hashlib
import json
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)

TEXT_SEARCH_URL = 'https://places.googleapis.com/v1/places:searchText'
DETAILS_URL = 'https://places.googleapis.com/v1/places/{id}'
GEOCODE_URL = 'https://maps.googleapis.com/maps/api/geocode/json'
TIMEOUT = 8
COORDS_TTL = 30 * 24 * 3600   # latitude / longitude (+ place id): ≤ 30 consecutive calendar days
CONTENT_TTL = 24 * 3600       # other Places content (labels, addresses, websites): a day
NEGATIVE_TTL = 30 * 24 * 3600  # "nothing found"
APIS = ('places_text', 'places_details', 'geocode')
_MISS = object()


def key_configured():
    return bool(getattr(settings, 'GOOGLE_PLACES_API_KEY', ''))


# ---------------------------------------------------------------------------
# Counters and quota
# ---------------------------------------------------------------------------

def _today():
    return datetime.now(ZoneInfo('America/Los_Angeles')).date().isoformat()  # Google's quota day


def _count(api, what, n=1):
    key = f'google:n:{_today()}:{api}:{what}'
    try:
        cache.incr(key, n)
    except ValueError:
        cache.set(key, n, 3 * 24 * 3600)


def usage(day=None):
    """{api: {calls, hits, errors, quota}} for one Pacific day (default today)."""
    day = day or _today()
    keys = {f'google:n:{day}:{api}:{w}': (api, w) for api in APIS for w in ('calls', 'hits', 'errors', 'quota')}
    got = cache.get_many(list(keys))
    out = {api: {'calls': 0, 'hits': 0, 'errors': 0, 'quota': 0} for api in APIS}
    for k, v in got.items():
        api, w = keys[k]
        out[api][w] = v
    return out


def _quota_key(api):
    return f'google:quota:{_today()}:{api}'


def over_quota(api):
    return bool(cache.get(_quota_key(api)))


def _quota_hit(api, detail=''):
    if not cache.get(_quota_key(api)):
        logger.warning('Google %s quota reached (%s): answering "nothing" until midnight Pacific', api, detail[:120])
    _count(api, 'quota')
    now = datetime.now(ZoneInfo('America/Los_Angeles'))
    midnight = datetime.combine(now.date() + timedelta(days=1), datetime.min.time(), now.tzinfo)
    cache.set(_quota_key(api), True, max(60, int((midnight - now).total_seconds())))


# ---------------------------------------------------------------------------
# Calls
# ---------------------------------------------------------------------------

def _hash(*parts):
    return hashlib.sha256(json.dumps(parts, default=str, ensure_ascii=False).encode()).hexdigest()[:40]


def _norm(text):
    return ' '.join((text or '').lower().split())[:300]


def _area(near):
    return (round(float(near[0]), 2), round(float(near[1]), 2)) if near else None


def _request(api, method, url, **kwargs):
    """The JSON body, or None (an error or a quota refusal: never raises, never retries)."""
    import httpx
    if over_quota(api):
        _count(api, 'quota')
        return None
    _count(api, 'calls')
    try:
        resp = httpx.request(method, url, timeout=TIMEOUT, **kwargs)
    except Exception as exc:
        _count(api, 'errors')
        logger.warning('Google %s failed: %s', api, exc.__class__.__name__)
        return None
    body = {}
    try:
        body = resp.json()
    except ValueError:
        pass
    status = (body.get('error') or {}).get('status', '') if isinstance(body.get('error'), dict) else ''
    if resp.status_code == 429 or status == 'RESOURCE_EXHAUSTED' or body.get('status') == 'OVER_QUERY_LIMIT':
        _quota_hit(api, status or body.get('status', '') or str(resp.status_code))
        return None
    if resp.status_code >= 400:
        _count(api, 'errors')
        logger.warning('Google %s answered %s %s', api, resp.status_code, status)
        return None
    logger.info('Google %s call', api)
    return body


def text_search(query, near=None, radius=50000, limit=1, website=False):
    """[{place_id, lat, lng, label, address, website?}] (may be empty). Coordinates and place ids are kept 30 days,
    labels / addresses / websites a day."""
    query = ' '.join((query or '').split())[:300]
    if not query or not key_configured():
        return []
    base = _hash('text', _norm(query), _area(near), radius if near else None, limit, website)
    content = cache.get(f'google:tc:{base}', _MISS)
    coords = cache.get(f'google:tg:{base}', _MISS)
    if content is not _MISS and coords is not _MISS:
        _count('places_text', 'hits')
        return [{**c, **t} for c, t in zip(coords, content)]
    body = {'textQuery': query, 'pageSize': limit}
    if near:
        body['locationBias'] = {'circle': {'center': {'latitude': float(near[0]), 'longitude': float(near[1])},
                                           'radius': radius}}
    mask = 'places.id,places.displayName,places.formattedAddress,places.location' + (',places.websiteUri' if website else '')
    data = _request('places_text', 'POST', TEXT_SEARCH_URL, json=body,
                    headers={'X-Goog-Api-Key': settings.GOOGLE_PLACES_API_KEY, 'X-Goog-FieldMask': mask})
    if data is None:
        return []
    geo, text = [], []
    for p in data.get('places') or []:
        loc = p.get('location') or {}
        if loc.get('latitude') is None:
            continue
        geo.append({'place_id': p.get('id', ''), 'lat': loc['latitude'], 'lng': loc['longitude']})
        text.append({'label': (p.get('displayName') or {}).get('text') or p.get('formattedAddress', ''),
                     'address': p.get('formattedAddress', ''), **({'website': p.get('websiteUri') or ''} if website else {})})
    ttl = COORDS_TTL if geo else NEGATIVE_TTL
    cache.set(f'google:tg:{base}', geo, ttl)
    cache.set(f'google:tc:{base}', text, CONTENT_TTL if geo else NEGATIVE_TTL)
    return [{**c, **t} for c, t in zip(geo, text)]


def place_website(place_id):
    """The place's website (Place Details, ``websiteUri`` only), '' when it has none. Cached a day ('' 30 days)."""
    if not place_id or not key_configured():
        return ''
    key = f'google:web:{_hash(place_id)}'
    found = cache.get(key, _MISS)
    if found is not _MISS:
        _count('places_details', 'hits')
        return found
    data = _request('places_details', 'GET', DETAILS_URL.format(id=place_id),
                    headers={'X-Goog-Api-Key': settings.GOOGLE_PLACES_API_KEY, 'X-Goog-FieldMask': 'websiteUri'})
    if data is None:
        return ''
    site = data.get('websiteUri') or ''
    cache.set(key, site, CONTENT_TTL if site else NEGATIVE_TTL)
    return site


def geocode(address):
    """{place_id, lat, lng, address} or None. Coordinates and place id 30 days, the formatted address a day."""
    address = ' '.join((address or '').split())[:300]
    if not address or not key_configured():
        return None
    base = _hash('geo', _norm(address))
    coords = cache.get(f'google:gg:{base}', _MISS)
    if coords is not _MISS:
        _count('geocode', 'hits')
        if not coords:
            return None
        return {**coords, 'address': cache.get(f'google:gc:{base}') or ''}
    data = _request('geocode', 'GET', GEOCODE_URL, params={'address': address, 'key': settings.GOOGLE_PLACES_API_KEY})
    if data is None:
        return None
    results = data.get('results') or []
    if not results:
        if data.get('status') in ('ZERO_RESULTS', 'OK'):
            cache.set(f'google:gg:{base}', {}, NEGATIVE_TTL)
        else:
            _count('geocode', 'errors')
        return None
    first = results[0]
    loc = first['geometry']['location']
    coords = {'place_id': first.get('place_id', ''), 'lat': loc['lat'], 'lng': loc['lng']}
    cache.set(f'google:gg:{base}', coords, COORDS_TTL)
    cache.set(f'google:gc:{base}', first.get('formatted_address', ''), CONTENT_TTL)
    return {**coords, 'address': first.get('formatted_address', '')}
