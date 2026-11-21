"""Onboarding (owner, Nov 21, 2026): "Try it on your event" on the home page and the console's first screen.

- ``gallery(request, near=None)``: the "See it live" cards: showcase events, the DEMO event, and first the published
  prospect test events "Happening near you" (claimable). Each card: cover, title, type, dates, the theme's colours, a
  QR code that joins (phones: Open in Attnd).
- ``save_pending(request, …)`` / ``consume(request)``: the composer's text and file wait in a ``PendingImport`` (its id
  in the visitor's session) until they have an account; then it becomes their AI import (a demo event they own).
- ``may_import(user)``: the daily cap of AI imports for new accounts.
"""

import math
import re
from datetime import timedelta

from django.conf import settings
from django.urls import reverse
from django.utils import timezone

SESSION_KEY = 'pending_import'
PENDING_HOURS = 24
MAX_TEXT = 8000
MAX_FILE_MB = 15
# New accounts (younger than NEW_ACCOUNT_DAYS) start at most this many AI imports a day.
NEW_ACCOUNT_DAYS = 7
NEW_ACCOUNT_DAILY_IMPORTS = 3
NEAR_MILES = 150

CHIPS = [
    {'key': 'website', 'label': 'Import from a website', 'prompt': 'https://', 'look': 'festival'},
    {'key': 'flyer', 'label': 'Start from a flyer', 'prompt': 'Here’s our flyer: ', 'look': 'market', 'attach': True},
    {'key': 'festival', 'label': 'Plan a festival', 'look': 'festival',
     'prompt': 'A two-day music festival downtown with three stages, food trucks and a kids’ area.'},
    {'key': 'market', 'label': 'Plan a market', 'look': 'market',
     'prompt': 'A Saturday farmers market in the park: 30 stalls, live music at noon, a cooking demo at 10.'},
    {'key': 'looking', 'label': 'Just looking', 'look': 'conference', 'href': '#see-it-live'},
]

PLACEHOLDERS = [
    'Paste your event’s website…',
    'A food truck rally on Main Street, Saturday 11–8…',
    'Our conference: two days, four rooms, 40 talks…',
    'Drop a flyer, a PDF program or a spreadsheet…',
]


# ---------------------------------------------------------------------------
# The gallery
# ---------------------------------------------------------------------------

def _miles(a_lat, a_lng, b_lat, b_lng):
    lat1, lng1, lat2, lng2 = map(math.radians, (float(a_lat), float(a_lng), float(b_lat), float(b_lng)))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lng2 - lng1) / 2) ** 2
    return 3958.8 * 2 * math.asin(math.sqrt(h))


def _dates(event):
    from zoneinfo import ZoneInfo
    if not event.start_time:
        return ''
    try:
        tz = ZoneInfo(event.timezone or 'UTC')
    except Exception:
        tz = ZoneInfo('UTC')
    start = event.start_time.astimezone(tz)
    end = event.end_time.astimezone(tz) if event.end_time else None
    if end and end.date() != start.date():
        if end.month == start.month:
            return f'{start:%b} {start.day}–{end.day}'
        return f'{start:%b} {start.day} – {end:%b} {end.day}'
    return f'{start:%a, %b} {start.day}'


def _cover(event):
    """(url, credit): the event's own flyer or a place photo first, then the gallery cover events/covers.py found
    (a Wikimedia Commons photo with its credit, or an AI picture)."""
    if event.flyer and not (event.flyer.name or '').lower().endswith('.pdf'):
        try:
            return event.flyer.url, ''
        except Exception:
            pass
    loc = event.location_set.filter(hidden=False).exclude(image='').exclude(image__isnull=True).first()
    if loc is not None:
        try:
            return loc.image.url, loc.image_credit or ''
        except Exception:
            pass
    if event.cover:
        try:
            return event.cover.url, event.cover_credit
        except Exception:
            pass
    loc = event.location_set.filter(hidden=False).exclude(image_url='').exclude(image_url__isnull=True).first()
    return (loc.image_url if loc else '') or event.logo_url or '', ''


def _website(event):
    """The event's own website for "Event website ↗": the published Studio site, else its first source page; https
    only. Showcase events are made up and have none."""
    if event.showcase:
        return ''
    from events.content import _website_url
    from events.context import https_url
    url = _website_url(event) or ''
    if not url:
        urls = [u for u in (event.source_urls or []) if isinstance(u, str)]
        url = urls[0] if urls else ''
    return https_url(url)


def _colours(event):
    from events import theme as T
    theme = T.normalize(event.theme) or T.default_theme()
    light = theme['light']
    return {'primary': light['primary'], 'secondary': light['secondary'], 'tertiary': light['tertiary']}


def card(event, kind='', near=None, claimable=False):
    from attnd.links import join_url, open_in_app_links
    from events.showcase import kind_of
    cover, credit = _cover(event)
    miles = None
    if near and event.latitude is not None and event.longitude is not None:
        miles = round(_miles(near[0], near[1], event.latitude, event.longitude))
    return {
        'id': event.pk, 'code': event.event_code, 'name': event.name, 'kind': kind or kind_of(event),
        'dates': _dates(event), 'city': (event.address or '').split(',')[-2].strip() if event.address.count(',') >= 1 else '',
        'cover': cover, 'credit': credit, 'website': _website(event), 'colours': _colours(event), 'join': join_url(event),
        'qr': reverse('join:join_qr', args=[event.event_code]), 'open': open_in_app_links(event),
        'lat': float(event.latitude) if event.latitude is not None else None,
        'lng': float(event.longitude) if event.longitude is not None else None,
        'miles': miles, 'claimable': claimable,
        'claim': reverse('home:claim', args=[event.event_code]) if claimable else '',
    }


def gallery(near=None):
    """(near_you, showcase): cards for the "See it live" section."""
    from events.apis import demo_event
    from events.models import Event
    live = Event.objects.filter(status=Event.STATUS_PUBLISHED)
    showcase = [card(e) for e in live.filter(showcase=True).order_by('name')]
    demo = demo_event()
    if demo is not None and not demo.showcase:
        showcase.append(card(demo, kind='Festival · the demo'))
    near_you = []
    soon = timezone.now() + timedelta(days=120)
    candidates = (live.filter(testing_only=True, prospects__isnull=False, end_time__gte=timezone.now(),
                              start_time__lte=soon, latitude__isnull=False, longitude__isnull=False)
                  .exclude(claim_requests__status='approved').distinct()[:40])
    for e in candidates:
        c = card(e, kind='Near you', near=near, claimable=True)
        if near is None or (c['miles'] is not None and c['miles'] <= NEAR_MILES):
            near_you.append(c)
    near_you.sort(key=lambda c: (c['miles'] if c['miles'] is not None else 10 ** 6, c['name']))
    return near_you[:8], showcase


def categories(cards):
    seen = []
    for c in cards:
        k = c['kind'].split(' · ')[0]
        if k not in seen:
            seen.append(k)
    return seen


def clean_region(text):
    """The prospects region as a short label: its first line, empty parts dropped ("Fort Worth, TX, " → "Fort Worth,
    TX"), no trailing punctuation."""
    line = next((ln for ln in (text or '').splitlines() if ln.strip()), '')
    parts = [p.strip(' .;:-–—') for p in re.split(r'[,;]', line)]
    return ', '.join(p for p in parts if p)[:80]


def region():
    try:
        from prospects.models import ProspectSettings
        s = ProspectSettings.objects.filter(pk=1).first()
        return clean_region(s.region) if s else ''
    except Exception:
        return ''


def near_place(cards):
    """"Happening near <city>": the nearest card's city (the visitor's own place is never looked up)."""
    for c in cards:
        if c.get('city'):
            return c['city']
    return ''


# ---------------------------------------------------------------------------
# Pending imports
# ---------------------------------------------------------------------------

_URL = re.compile(r'https?://[^\s<>"\']+', re.I)


def sniff(data, name=''):
    """The upload's type from its first bytes (PDF, images, XLSX, CSV / text); '' when not allowed."""
    head = data[:16]
    if head.startswith(b'%PDF'):
        return 'application/pdf'
    if head.startswith(b'\x89PNG'):
        return 'image/png'
    if head.startswith(b'\xff\xd8\xff'):
        return 'image/jpeg'
    if head[:4] == b'RIFF' and data[8:12] == b'WEBP':
        return 'image/webp'
    if head.startswith(b'PK\x03\x04') and name.lower().endswith('.xlsx'):
        return 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    try:
        data[:4096].decode('utf-8')
        return 'text/csv' if name.lower().endswith('.csv') else 'text/plain'
    except UnicodeDecodeError:
        return ''


def save_pending(request, text, upload=None, time_zone=''):
    """Keep what the visitor gave; returns (PendingImport, error)."""
    from home.models import PendingImport
    text = (text or '').strip()[:MAX_TEXT]
    data, kind, name = None, '', ''
    if upload is not None:
        if upload.size > MAX_FILE_MB * 1024 * 1024:
            return None, f'That file is over {MAX_FILE_MB} MB.'
        data = upload.read()
        name = (upload.name or 'upload')[:256]
        kind = sniff(data, name)
        if not kind:
            return None, 'Attach a PDF, an image, a spreadsheet or a text file.'
    if not text and data is None:
        return None, 'Tell us about your event, paste its website or attach a flyer.'
    pending = PendingImport.objects.create(text=text, urls=_URL.findall(text)[:5], upload_name=name, upload_type=kind,
                                           upload_data=data, time_zone=(time_zone or '')[:64])
    request.session[SESSION_KEY] = str(pending.pk)
    return pending, ''


def pending_for(request):
    from home.models import PendingImport
    pk = request.session.get(SESSION_KEY)
    if not pk:
        return None
    cutoff = timezone.now() - timedelta(hours=PENDING_HOURS)
    return PendingImport.objects.filter(pk=pk, used_at__isnull=True, created_at__gte=cutoff).first()


def may_import(user):
    """New accounts start at most NEW_ACCOUNT_DAILY_IMPORTS AI imports a day (the AI cost cap for new accounts)."""
    from scrape.models import IngestJob
    if user.is_staff or user.is_superuser:
        return True
    if user.date_joined < timezone.now() - timedelta(days=NEW_ACCOUNT_DAYS):
        return True
    since = timezone.now() - timedelta(days=1)
    return IngestJob.objects.filter(created_by=user, kind=IngestJob.KIND_CREATE, created_at__gte=since).count() \
        < int(getattr(settings, 'NEW_ACCOUNT_DAILY_IMPORTS', NEW_ACCOUNT_DAILY_IMPORTS))


def consume(request):
    """The visitor's pending import → their AI import (a demo event). Returns (job, error); (None, '') without one."""
    from scrape import jobs
    from scrape.models import IngestJob
    pending = pending_for(request)
    if pending is None:
        return None, ''
    if not may_import(request.user):
        return None, 'New accounts can build three events a day. Try again tomorrow, or edit the ones you have.'
    text = pending.text
    urls = [u for u in pending.urls if u.strip() != text.strip()] if len(text) > len(' '.join(pending.urls)) + 3 else pending.urls
    job = IngestJob.objects.create(
        kind=IngestJob.KIND_CREATE, trigger=IngestJob.TRIGGER_HOME, created_by=request.user,
        urls=pending.urls, text='' if text.strip() in pending.urls else text,
        upload_name=pending.upload_name, upload_type=pending.upload_type, upload_data=pending.upload_data,
        timezone=pending.time_zone)
    del urls
    pending.used_by = request.user
    pending.used_at = timezone.now()
    pending.job_id = job.pk
    pending.upload_data = None
    pending.save(update_fields=['used_by', 'used_at', 'job_id', 'upload_data'])
    request.session.pop(SESSION_KEY, None)
    jobs.enqueue(job)
    return job, ''


def purge_pending():
    from home.models import PendingImport
    cutoff = timezone.now() - timedelta(hours=PENDING_HOURS)
    return PendingImport.objects.filter(created_at__lt=cutoff).delete()[0]


# ---------------------------------------------------------------------------
# The page (the home page and the console's first screen share it)
# ---------------------------------------------------------------------------

def looks():
    """The hero's phone preview: one look per chip, drawn from the showcase specs (no database needed, so it also
    works where the showcase events aren't loaded)."""
    from events import theme as T
    from events.showcase import SHOWCASE
    by_code = {s['code']: s for s in SHOWCASE}
    out = []
    for key, code in (('festival', 'SHOWFEST'), ('market', 'SHOWMARKET'), ('conference', 'SHOWCONF')):
        spec = by_code[code]
        theme = T.normalize({'light': dict(zip(T.ROLES, spec['theme']))})
        tokens = T.tokens(theme['light'], 'light')
        out.append({'key': key, 'name': spec['name'], 'kind': spec['kind'], 'address': spec['address'],
                    'places': [p[0] for p in spec['places'][:3]],
                    'items': [i[0] for i in spec['items'][1:4]],
                    'style': ';'.join(f'--ph-{k}:{v}' for k, v in (
                        ('brand', tokens['--color-brand']), ('on', tokens['--color-on-brand']),
                        ('soft', tokens['--color-brand-soft']), ('text', tokens['--color-brand-text']),
                        ('accent', theme['light']['tertiary'])))})
    return out


GALLERY_CACHE_SECONDS = 120


def cached_gallery():
    """The page's own gallery (no visitor location) for a couple of minutes per process: the home page is the
    busiest public page, and the cards change only when the showcase events roll over or a test event is claimed."""
    from django.core.cache import caches
    cache = caches['local']
    data = cache.get('home:gallery')
    if data is None:
        data = (gallery(None), region())
        cache.set('home:gallery', data, GALLERY_CACHE_SECONDS)
    return data


def page_context(request, preview=False, near=None):
    (near_you, showcase_cards), where = cached_gallery() if near is None else (gallery(near), region())
    return {
        'chips': CHIPS, 'placeholders': PLACEHOLDERS, 'looks': looks(), 'near_you': near_you,
        'showcase': showcase_cards, 'categories': categories(showcase_cards), 'region': where,
        'preview': preview, 'free_limit': int(getattr(settings, 'FREE_DEVICE_LIMIT', 20)),
        'max_file_mb': MAX_FILE_MB, 'geo_key': getattr(settings, 'GOOGLE_MAPS_BROWSER_KEY', ''),
    }
