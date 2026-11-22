"""Covers for the home page gallery's cards (owner, Nov 21, 2026).

Every card gets a picture, in this order:
1. the event's own flyer or a place photo (home.onboarding._cover; nothing to do here),
2. a licensed photo from Wikimedia Commons near the event's venue (geosearch around its pin, then its venue / city by
   name), checked by Claude Haiku's vision like the place images (events/place_images.py) and stored with its credit
   (author · licence · Wikimedia Commons), shown small on the card,
3. else an AI-made picture (Studio's image model, like the showcase events' covers).

Never an image from the organizer's own website (copyright). Stored in ``Event.cover`` (not the flyer, so the apps
don't change); not part of the API. ``ensure(event)`` runs when a prospect test event's import finishes
(scrape.jobs), ``manage.py find_event_covers`` backfills. Every look's cost goes into ``Event.cover_cost_usd`` and
``EVENT_COVERS_DAILY_CAP_USD`` (default 2) caps all of them per UTC day.
"""

import hashlib
import json
import logging
import re
import threading
from decimal import Decimal
from urllib.parse import quote

from django.conf import settings
from django.db.models import Sum
from django.utils import timezone

from events import place_images as P

logger = logging.getLogger(__name__)

GEO_RADIUS_M = 1000
# A new event's place photos are looked for in the background first; its cover waits for them.
AFTER_IMPORT_SECONDS = 120
CHECK_PROMPT = """You check a candidate photo for the cover of an event's card on a website (a festival, market, \
fair, race, art walk…). Say whether it is a real, attractive photo of the event's venue, its area or a past edition \
that would look right as the card's background. Logos, maps, icons, flyers, posters, text graphics, close-up portraits \
of people and photos of somewhere else are not usable. Be strict: when in doubt, it is not usable."""


def spent_today():
    from events.models import Event
    start = timezone.now().replace(hour=0, minute=0, second=0, microsecond=0)
    return Event.objects.filter(cover_checked_at__gte=start).aggregate(t=Sum('cover_cost_usd'))['t'] or Decimal(0)


def budget_left():
    from credit.ledger import limits
    return Decimal(limits().covers_daily_usd) - spent_today()


def has_own_picture(event):
    if event.flyer and not (event.flyer.name or '').lower().endswith('.pdf'):
        return True
    locs = event.location_set.filter(hidden=False)
    return locs.exclude(image='').exclude(image__isnull=True).exists()


def wanted(event):
    """Gallery events (published prospect test events and showcase events) without a picture of their own."""
    return (event.status == event.STATUS_PUBLISHED and (event.testing_only or event.showcase)
            and not event.cover and not has_own_picture(event))


def _venue(event):
    first = event.location_set.filter(hidden=False).order_by('sort_order', 'pk').first()
    parts = [p.strip() for p in (event.address or '').split(',') if p.strip()]
    city = parts[-2] if len(parts) >= 2 else (parts[0] if parts else '')
    return (first.name if first else ''), city


def candidates(event):
    """Commons files near the event's pin, then named like its venue or event in its city (author and licence)."""
    titles = []
    if event.latitude is not None and event.longitude is not None:
        data = P._commons({'action': 'query', 'list': 'geosearch', 'gscoord': f'{float(event.latitude)}|'
                           f'{float(event.longitude)}', 'gsradius': GEO_RADIUS_M, 'gsnamespace': 6, 'gslimit': 10})
        titles += [g['title'] for g in (data.get('query') or {}).get('geosearch') or []]
    venue, city = _venue(event)
    for words in (f'"{venue}" {city}' if venue else '', f'"{event.name}" {city}'):
        if words.strip():
            data = P._commons({'action': 'query', 'list': 'search', 'srnamespace': 6, 'srlimit': 4,
                               'srsearch': words.strip()})
            titles += [s['title'] for s in (data.get('query') or {}).get('search') or []]
    titles = [t for t in dict.fromkeys(titles)
              if re.search(r'\.(jpe?g|png|webp)$', t, re.IGNORECASE) and not P.LOGO_WORDS.search(t)][:8]
    if not titles:
        return []
    data = P._commons({'action': 'query', 'prop': 'imageinfo', 'titles': '|'.join(titles),
                       'iiprop': 'url|size|extmetadata', 'iiurlwidth': 1600})
    out = []
    for page in (data.get('query') or {}).get('pages') or []:
        info = (page.get('imageinfo') or [{}])[0]
        if (info.get('width') or 0) < P.MIN_WIDTH or (info.get('width') or 0) < (info.get('height') or 0):
            continue  # covers are landscape
        meta = info.get('extmetadata') or {}
        license_name = P._strip_html((meta.get('LicenseShortName') or {}).get('value'))
        if not license_name or 'fair use' in license_name.lower():
            continue
        artist = P._strip_html((meta.get('Artist') or {}).get('value'))[:120]
        credit = f'{artist + " · " if artist else ""}{license_name} · Wikimedia Commons'
        url = info.get('thumburl') or info.get('url')
        if url:
            out.append(P.Candidate(url, 'wikimedia', credit=credit[:300], license=license_name[:120],
                                   page=info.get('descriptionurl')
                                   or f'https://commons.wikimedia.org/wiki/{quote(page["title"])}'))
    return out[:4]


def confirm(event, jpeg):
    from attnd import claude
    venue, city = _venue(event)
    facts = {'event': event.name, 'about': (event.description or '')[:300], 'venue': venue, 'city': city,
             'address': event.address}
    data, cost = claude.structured(system=CHECK_PROMPT, schema=P.CHECK_SCHEMA, effort='low', max_tokens=400,
                                   content=[claude.image_block(jpeg, 'image/jpeg'),
                                            {'type': 'text', 'text': f'The event (data): {json.dumps(facts)}'}])
    return (bool(data.get('usable')), float(data.get('confidence') or 0), str(data.get('kind') or ''), cost)


def from_wikimedia(event):
    """(jpeg, credit, cost) or (None, '', cost)."""
    from attnd import claude
    cost = Decimal(0)
    if not claude.available():
        return None, '', cost
    for candidate in candidates(event):
        if budget_left() - cost <= 0:
            break
        got = P._fetch(candidate.url, P.MAX_BYTES)
        image = P._load_image(got[0]) if got else None
        if image is None or image.width < P.MIN_WIDTH:
            continue
        jpeg = P.reencode(image)
        try:
            usable, confidence, kind, spent = confirm(event, jpeg)
        except Exception as exc:  # the AI check failing never stops the next candidate
            logger.info('Cover check failed: %s', exc.__class__.__name__)
            continue
        cost += Decimal(str(spent or 0))
        if usable and kind == 'photo' and confidence >= getattr(settings, 'PLACE_IMAGES_MIN_CONFIDENCE', 0.75):
            return jpeg, candidate.credit, cost
    return None, '', cost


def from_ai(event):
    """(jpeg, cost) from Studio's image model, or (None, 0) without OpenAI or on an error."""
    if not getattr(settings, 'OPENAI_API_KEY', '') and not _openai_env():
        return None, Decimal(0)
    price = Decimal(str(settings.STUDIO_PRICES.get('image', 0.25)))
    if budget_left() < price:
        return None, Decimal(0)
    from studio.ai import generate_image
    venue, city = _venue(event)
    prompt = (f'A bright, inviting photo-style illustration for the event "{event.name}"'
              f'{" in " + city if city else ""}{" at " + venue if venue else ""}. {(event.description or "")[:400]} '
              'Wide landscape scene of the setting and atmosphere. No text, no letters, no logos, no faces in focus.')
    try:
        png, _model = generate_image(prompt, size='landscape')
    except Exception as exc:
        logger.info('AI cover failed: %s', exc.__class__.__name__)
        return None, price
    image = P._load_image(png)
    return (P.reencode(image) if image is not None else None), price


def _openai_env():
    import os
    return bool(os.environ.get('OPENAI_API_KEY'))


def ensure(event, ai=True, force=False):
    """(AI credit: Attnd's own marketing, exempt from the owner's monthly limit.)"""
    from credit import ledger
    with ledger.spend_context(event=event, feature='covers', exempt=True):
        return _ensure(event, ai, force)


def _ensure(event, ai=True, force=False):
    """Give a gallery event a cover when it has no picture of its own. Returns 'wikimedia', 'ai', 'own', 'skipped'
    or 'none'."""
    from django.core.files.base import ContentFile
    if has_own_picture(event):
        return 'own'
    if not force and (event.cover or not wanted(event)):
        return 'skipped'
    if budget_left() <= 0:
        return 'skipped'
    # Showcase events are made up: a real photo of the spot would mislead, so they get an AI picture.
    jpeg, credit, cost = (None, '', Decimal(0)) if event.showcase else from_wikimedia(event)
    source = 'wikimedia' if jpeg else ''
    if jpeg is None and ai:
        jpeg, ai_cost = from_ai(event)
        cost += ai_cost
        source, credit = ('ai', 'AI-generated image') if jpeg else ('', '')
    event.cover_checked_at = timezone.now()
    event.cover_cost_usd = (event.cover_cost_usd or 0) + cost
    fields = ['cover_checked_at', 'cover_cost_usd']
    if jpeg:
        digest = hashlib.sha256(jpeg).hexdigest()[:12]
        event.cover.save(f'cover-{event.pk}-{digest}.jpg', ContentFile(jpeg), save=False)
        event.cover_credit, event.cover_source = credit[:300], source
        fields += ['cover', 'cover_credit', 'cover_source']
    event.save(update_fields=fields)
    _forget_gallery()
    return source or 'none'


def _forget_gallery():
    from django.core.cache import caches
    caches['local'].delete('home:gallery')


def schedule(event_id, delay=0):
    """In the background after commit (the import's job thread must not wait for image models)."""
    if getattr(settings, 'TESTING', False) and not getattr(settings, 'EVENT_COVERS_IN_TESTS', False):
        return

    def run():
        import time

        from django.db import close_old_connections
        if delay:
            time.sleep(delay)

        from events.models import Event
        try:
            event = Event.objects.filter(pk=event_id).first()
            if event is not None:
                ensure(event)
        except Exception:
            logger.exception('Event cover failed for %s', event_id)
        finally:
            close_old_connections()

    threading.Thread(target=run, daemon=True, name=f'cover-{event_id}').start()


def due(limit=10):
    """Gallery events never looked at (run_scheduled_tasks catches what a restarted dyno missed)."""
    from datetime import timedelta

    from django.db.models import Q

    from events.models import Event
    old = timezone.now() - timedelta(minutes=15)
    rows = (Event.objects.filter(status=Event.STATUS_PUBLISHED, cover='', cover_checked_at__isnull=True,
                                 created_at__lte=old)
            .filter(Q(testing_only=True, prospects__isnull=False) | Q(showcase=True)).distinct()[:limit])
    return [e for e in rows if wanted(e)]


def refresh_due(stdout=None):
    if getattr(settings, 'TESTING', False) and not getattr(settings, 'EVENT_COVERS_IN_TESTS', False):
        return 0
    done = 0
    for event in due():
        if budget_left() <= 0:
            break
        result = ensure(event)
        done += 1
        if stdout is not None:
            stdout.write(f'cover {event.event_code}: {result}')
    return done
