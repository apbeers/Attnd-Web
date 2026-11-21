"""Place images in our own store (stage D).

A location without an image gets one found automatically, stored in our media storage (Cloudinary in production) as
`Location.image`, with where it came from, its credit and licence:

1. the event's own pages (the organizer's material): images on the event's source pages whose alt text, caption or
   file name names the place;
2. the place's official website (`Location.website`, else Google Places' `websiteUri` for its name): og:image /
   twitter:image, then the first big image;
3. Wikimedia Commons, by coordinates (within 300 m) and by name: the licence and the author are recorded.

No Google Places photos (their terms forbid storing them and their links expire); Places only finds the website.

Every candidate must be at least 600 px wide, not a logo / map / icon (by its name and then by Claude), not a copy of
another place's image (SHA-256), and Claude Haiku (vision, structured output) must say with confidence that it is a
usable photo of THIS place. Downloads go through Studio's SSRF-guarded fetcher; the picture is re-encoded with Pillow
(EXIF and GPS dropped, at most 2000 px, JPEG). Organizer images, locked images and places whose found image was
removed (`image_never_refetch`) are never touched. A daily cost cap (`PLACE_IMAGES_DAILY_CAP_USD`) covers every look.
"""

import hashlib
import io
import json
import logging
import re
import threading
from decimal import Decimal
from urllib.parse import quote, urljoin

from django.conf import settings
from django.db import close_old_connections, connection, transaction
from django.utils import timezone

logger = logging.getLogger(__name__)

MIN_WIDTH = 600
MAX_SIDE = 2000
MAX_BYTES = 12 * 1024 * 1024
LOGO_WORDS = re.compile(r'logo|icon|sprite|favicon|avatar|badge|banner-ad|map|marker|placeholder|button|qr', re.IGNORECASE)
COMMONS_API = 'https://commons.wikimedia.org/w/api.php'
USER_AGENT = 'AttndPlaceImages/1.0 (+https://attnd.app)'

CHECK_SCHEMA = {
    'type': 'object', 'additionalProperties': False, 'required': ['usable', 'confidence', 'kind', 'reason'],
    'properties': {
        'usable': {'type': 'boolean', 'description': 'A real photo that shows this place (its building, stage, '
                                                     'booth, stall, food or setting) and suits an app for visitors'},
        'confidence': {'type': 'number', 'description': '0 to 1: how sure you are it shows THIS place'},
        'kind': {'type': 'string', 'enum': ['photo', 'logo', 'map', 'icon', 'illustration', 'text', 'other']},
        'reason': {'type': 'string', 'description': 'One short sentence'},
    },
}
CHECK_PROMPT = """You check a candidate image for a place in an event app (a stage, venue, restaurant, booth, shop…). \
Say whether it is a real photo that shows THIS place and would look right as its picture for visitors. Logos, maps, \
icons, flyers, text graphics, people-only portraits and photos of somewhere else are not usable. Be strict: when in \
doubt, it is not usable."""


class Candidate:
    def __init__(self, url, source, credit='', license='', page=''):
        self.url, self.source, self.credit, self.license, self.page = url, source, credit, license, page


def enabled():
    from attnd import claude
    return bool(getattr(settings, 'PLACE_IMAGES_ENABLED', True)) and claude.available()


def spent_today():
    from django.db.models import Sum

    from events.models import PlaceImageAttempt
    start = timezone.now().replace(hour=0, minute=0, second=0, microsecond=0)
    return PlaceImageAttempt.objects.filter(created_at__gte=start).aggregate(t=Sum('cost_usd'))['t'] or Decimal(0)


def budget_left():
    return Decimal(str(getattr(settings, 'PLACE_IMAGES_DAILY_CAP_USD', 1))) - spent_today()


def wanted(location):
    """Only places without an organizer's image, not locked, not removed by the organizer."""
    locked = set(location.locked_fields or [])
    if location.image_never_refetch or {'image', 'image_url'} & locked:
        return False
    if location.image and not location.image_auto:
        return False
    if location.image_url and not _expiring(location.image_url):
        return False  # an organizer's (or the import's) own image link
    return not location.image


def _expiring(url):
    """Google Places photo links (from older imports): they expire and may not be stored."""
    return bool(re.search(r'googleusercontent\.com|places\.googleapis\.com|maps\.googleapis\.com/maps/api/place/photo',
                          url or ''))


# ---------------------------------------------------------------------------
# Candidates
# ---------------------------------------------------------------------------

def _fetch(url, limit=3 * 1024 * 1024):
    from studio.importer import SiteImportError, fetch
    try:
        return fetch(url, limit)
    except SiteImportError as exc:
        logger.info('Place image fetch refused/failed: %s', str(exc)[:120])
        return None


def _page_images(url, name=''):
    """[(image url, hint text)] from a page: og/twitter images first, then <img>s (with their alt / caption)."""
    got = _fetch(url)
    if not got or 'html' not in (got[1] or ''):
        return []
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(got[0].decode('utf-8', 'replace'), 'html.parser')
    base = got[2]
    out = []
    for prop in ('og:image', 'og:image:url', 'twitter:image'):
        tag = soup.find('meta', attrs={'property': prop}) or soup.find('meta', attrs={'name': prop})
        if tag and tag.get('content'):
            out.append((urljoin(base, tag['content'].strip()), 'og'))
    for img in soup.find_all('img')[:80]:
        src = img.get('src') or img.get('data-src') or ''
        if not src or src.startswith('data:'):
            continue
        caption = ''
        fig = img.find_parent('figure')
        if fig is not None and fig.find('figcaption'):
            caption = fig.find('figcaption').get_text(' ', strip=True)
        hint = ' '.join(filter(None, [img.get('alt'), img.get('title'), caption, src.rsplit('/', 1)[-1]]))
        out.append((urljoin(base, src.strip()), hint))
    seen, unique = set(), []
    for u, h in out:
        if u.startswith(('http://', 'https://')) and u not in seen and not LOGO_WORDS.search(u.rsplit('/', 1)[-1]):
            seen.add(u)
            unique.append((u, h))
    return unique


def _words(text):
    return {w for w in re.findall(r'[a-z0-9]{3,}', (text or '').lower()) if w not in {'the', 'and', 'stage', 'tent'}}


def from_event_pages(location, pages):
    """Images on the event's own pages whose alt text / caption / file name names this place."""
    name = _words(location.name)
    if not name:
        return []
    found = []
    for page_url, images in pages.items():
        for url, hint in images:
            if hint != 'og' and len(name & _words(hint)) >= max(1, (len(name) + 1) // 2):
                found.append(Candidate(url, 'event_page', page=page_url))
    return found[:4]


def venue_website(location):
    if location.website:
        return location.website
    key = getattr(settings, 'GOOGLE_PLACES_API_KEY', '')
    if not key or not location.name:
        return ''
    import httpx
    body = {'textQuery': f'{location.name} {location.address or location.event.address}'.strip(), 'pageSize': 1}
    if location.has_coordinates:
        body['locationBias'] = {'circle': {'center': {'latitude': float(location.latitude),
                                                      'longitude': float(location.longitude)}, 'radius': 300}}
    try:
        resp = httpx.post('https://places.googleapis.com/v1/places:searchText', json=body, timeout=8, headers={
            'X-Goog-Api-Key': key, 'X-Goog-FieldMask': 'places.websiteUri,places.displayName'})
        resp.raise_for_status()
        places = resp.json().get('places') or []
    except Exception as exc:
        logger.info('Places website lookup failed: %s', exc.__class__.__name__)
        return ''
    return (places[0].get('websiteUri') or '') if places else ''


def from_venue(location):
    site = venue_website(location)
    if not site:
        return []
    images = _page_images(site, location.name)
    ordered = [u for u, h in images if h == 'og'] + [u for u, h in images if h != 'og']
    return [Candidate(u, 'venue', page=site) for u in ordered[:3]]


def _commons(params):
    import httpx
    try:
        resp = httpx.get(COMMONS_API, params={**params, 'format': 'json', 'formatversion': 2}, timeout=10,
                         headers={'User-Agent': USER_AGENT})
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        logger.info('Wikimedia lookup failed: %s', exc.__class__.__name__)
        return {}


def _strip_html(text):
    return re.sub(r'<[^>]+>', '', text or '').strip()


def from_wikimedia(location):
    """Commons files near the place's pin (300 m) or named like it, with their author and licence."""
    titles = []
    if location.has_coordinates:
        data = _commons({'action': 'query', 'list': 'geosearch', 'gscoord': f'{float(location.latitude)}|'
                         f'{float(location.longitude)}', 'gsradius': 300, 'gsnamespace': 6, 'gslimit': 10})
        titles += [g['title'] for g in (data.get('query') or {}).get('geosearch') or []]
    data = _commons({'action': 'query', 'list': 'search', 'srnamespace': 6, 'srlimit': 5,
                     'srsearch': f'"{location.name}" {location.address.split(",")[-2] if "," in location.address else ""}'})
    titles += [s['title'] for s in (data.get('query') or {}).get('search') or []]
    titles = [t for t in dict.fromkeys(titles) if re.search(r'\.(jpe?g|png|webp)$', t, re.IGNORECASE)][:8]
    if not titles:
        return []
    data = _commons({'action': 'query', 'prop': 'imageinfo', 'titles': '|'.join(titles),
                     'iiprop': 'url|size|extmetadata', 'iiurlwidth': 1600})
    out = []
    for page in (data.get('query') or {}).get('pages') or []:
        info = (page.get('imageinfo') or [{}])[0]
        if (info.get('width') or 0) < MIN_WIDTH:
            continue
        meta = info.get('extmetadata') or {}
        license_name = _strip_html((meta.get('LicenseShortName') or {}).get('value'))
        if not license_name or 'fair use' in license_name.lower():
            continue
        artist = _strip_html((meta.get('Artist') or {}).get('value'))[:150]
        credit = f'{artist + " · " if artist else ""}{license_name} · Wikimedia Commons'
        url = info.get('thumburl') or info.get('url')
        if url:
            out.append(Candidate(url, 'wikimedia', credit=credit[:300], license=license_name[:120],
                                 page=info.get('descriptionurl') or f'https://commons.wikimedia.org/wiki/{quote(page["title"])}'))
    return out[:4]


# ---------------------------------------------------------------------------
# Checks and storing
# ---------------------------------------------------------------------------

def _load_image(data):
    from PIL import Image
    try:
        image = Image.open(io.BytesIO(data))
        image.load()
    except Exception:
        return None
    return image


def reencode(image):
    """JPEG, at most 2000 px, no EXIF / GPS (a fresh image from the pixels only)."""
    from PIL import Image, ImageOps
    image = ImageOps.exif_transpose(image)
    if image.mode not in ('RGB', 'L'):
        background = Image.new('RGB', image.size, (255, 255, 255))
        rgba = image.convert('RGBA')
        background.paste(rgba, mask=rgba.split()[-1])
        image = background
    image = image.convert('RGB')
    image.thumbnail((MAX_SIDE, MAX_SIDE))
    clean = Image.new('RGB', image.size)
    clean.paste(image)
    out = io.BytesIO()
    clean.save(out, 'JPEG', quality=85, optimize=True)
    return out.getvalue()


def image_digests(location):
    """The first 12 hex of each found image of the event (in the file names: place-<id>-<digest>.jpg), this place's own
    included, so Replace finds a different picture."""
    names = location.event.location_set.filter(image_auto=True).exclude(image='').values_list('image', flat=True)
    return {m.group(1) for n in names if (m := re.search(r'-([0-9a-f]{12})(?:_[A-Za-z0-9]+)?\.jpg$', n or ''))}


def confirm(location, jpeg):
    """(usable, confidence, kind, reason, cost) from Claude Haiku's vision."""
    from attnd import claude
    facts = {'place': location.name, 'category': location.category, 'summary': location.snippet,
             'address': location.address, 'event': location.event.name}
    data, cost = claude.structured(system=CHECK_PROMPT, schema=CHECK_SCHEMA, effort='low', max_tokens=400,
                                   content=[claude.image_block(jpeg, 'image/jpeg'),
                                            {'type': 'text', 'text': f'The place (data): {json.dumps(facts)}'}])
    return (bool(data.get('usable')), float(data.get('confidence') or 0), str(data.get('kind') or ''),
            str(data.get('reason') or '')[:200], cost)


def try_candidate(location, candidate, digests):
    """(jpeg bytes, digest, cost, why not) for one candidate."""
    got = _fetch(candidate.url, MAX_BYTES)
    if not got:
        return None, '', Decimal(0), 'could not download'
    image = _load_image(got[0])
    if image is None:
        return None, '', Decimal(0), 'not an image'
    if image.width < MIN_WIDTH:
        return None, '', Decimal(0), f'{image.width} px wide'
    jpeg = reencode(image)
    digest = hashlib.sha256(jpeg).hexdigest()
    if digest[:12] in digests:
        return None, '', Decimal(0), 'the same picture as another place'
    usable, confidence, kind, reason, cost = confirm(location, jpeg)
    if not usable or kind != 'photo' or confidence < getattr(settings, 'PLACE_IMAGES_MIN_CONFIDENCE', 0.75):
        return None, '', cost, f'not confident ({kind}, {confidence:.2f}): {reason}'
    return jpeg, digest, cost, ''


def store(location, jpeg, candidate, digest):
    from django.core.files.base import ContentFile
    location.image.save(f'place-{location.pk}-{digest[:12]}.jpg', ContentFile(jpeg), save=False)
    location.image_auto = True
    location.image_source_url = (candidate.page or candidate.url)[:1024]
    location.image_credit = candidate.credit or _credit_for(candidate)
    location.image_license = candidate.license
    location.image_fetched_at = timezone.now()
    if _expiring(location.image_url):
        location.image_url = None
    location.save()


def _credit_for(candidate):
    from urllib.parse import urlparse
    host = (urlparse(candidate.page or candidate.url).hostname or '').removeprefix('www.')
    return f'Photo: {host}'[:300] if host else ''


def find_for(location, pages=None, force=False):
    """Look for an image for one location; returns 'found', 'none', 'skipped' or 'error'."""
    from attnd import claude
    from events.models import PlaceImageAttempt
    if not force and not wanted(location):
        return 'skipped'
    if not enabled() or budget_left() <= 0:
        PlaceImageAttempt.objects.create(location=location, event=location.event, result='skipped',
                                         detail='disabled or over the daily cap')
        return 'skipped'
    digests = image_digests(location)
    if pages is None:
        pages = {u: _page_images(u) for u in (location.event.source_urls or [])[:3]}
    cost = Decimal(0)
    tried = []
    for finder in (lambda: from_event_pages(location, pages), lambda: from_venue(location),
                   lambda: from_wikimedia(location)):
        for candidate in finder():
            if budget_left() - cost <= 0:
                break
            try:
                jpeg, digest, spent, why = try_candidate(location, candidate, digests)
            except claude.ClaudeError as exc:
                tried.append(f'{candidate.source}: {exc}')
                continue
            cost += spent
            if jpeg is None:
                tried.append(f'{candidate.source}: {why}')
                continue
            store(location, jpeg, candidate, digest)
            PlaceImageAttempt.objects.create(location=location, event=location.event, source=candidate.source,
                                             result='found', cost_usd=cost, detail=candidate.url[:500])
            return 'found'
    PlaceImageAttempt.objects.create(location=location, event=location.event, result='none', cost_usd=cost,
                                     detail='; '.join(tried)[:500])
    if _expiring(location.image_url):  # a Google Places photo link from an older import: never kept
        location.image_url = None
        location.save(update_fields=['image_url'])
    return 'none'


def find_for_event(event, limit=None, force=False):
    """Every wanted location of an event (one fetch of its pages); returns counts."""
    pages = {u: _page_images(u) for u in (event.source_urls or [])[:3]}
    counts = {}
    for location in event.location_set.order_by('sort_order', 'name')[:limit]:
        try:
            result = find_for(location, pages, force=force)
        except Exception:
            logger.exception('Place image for location %s failed', location.pk)
            result = 'error'
        counts[result] = counts.get(result, 0) + 1
    return counts


_queue = None
_queue_lock = threading.Lock()


def _worker(q):
    while True:
        location_id = q.get()
        close_old_connections()
        try:
            from events.models import Location
            location = Location.objects.select_related('event').filter(pk=location_id).first()
            if location is not None:
                find_for(location)
        except Exception:
            logger.exception('Place image for location %s failed', location_id)
        finally:
            connection.close()
            q.task_done()


def schedule(location_id):
    """After a location is created (AI import, by hand, Ask Attnd): look once the save commits, one at a time in one
    background thread (an import of 40 places doesn't start 40 threads)."""
    if not getattr(settings, 'PLACE_IMAGES_ON_CREATE', True):
        return
    import background
    if background.database_queue():  # F5: a run_tasks worker picks it up once this transaction commits
        from events.tasks import find_place_image
        find_place_image.enqueue(location_id)
        return

    def put():
        global _queue
        import queue
        with _queue_lock:
            if _queue is None:
                _queue = queue.Queue()
                threading.Thread(target=_worker, args=(_queue,), daemon=True, name='place-images').start()
        _queue.put(location_id)
    transaction.on_commit(put)
