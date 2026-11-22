"""One image pipeline for everything the platform shows (owner, Nov 21, 2026).

- ``variant(url, width, height=None, crop=False)``: the image at that size. Cloudinary URLs get delivery transforms
  (``f_auto,q_auto`` + ``c_limit`` or ``c_fill``, exact pixels); our own media files without Cloudinary (local dev)
  get a Pillow-made copy in the media storage (``variants/``, made once, ``IMAGE_LOCAL_VARIANTS``); anything else is
  returned unchanged (static files, SVGs, PDFs, GIFs, other hosts until they are self-hosted).
- ``responsive(url, width, height=None, crop=False)``: ``(src, srcset)`` with 1x / 2x / 3x widths for ``<img>``;
  the ``{% img %}`` tag (events/templatetags/attnd_images.py) draws the whole element with width / height, ``sizes``,
  ``loading`` / ``decoding`` / ``fetchpriority``.
- ``self_host(url)``: a copy of an outside image in our own storage (Studio's SSRF-guarded fetcher, Pillow re-encode:
  JPEG, at most ``MAX_SIDE`` px, no EXIF / GPS). ``host_location(location)`` / ``host_event_logo(event)`` use it for
  ``Location.image_url`` and ``Event.logo_url``; Google Places photo links are never copied (their terms): they are
  dropped and the place-image finder looks for another. Run after a save (events/signals.py) and by
  ``manage.py self_host_images``.

The API's image URLs go through ``events.content.app_image_url``, which uses ``variant`` too.
"""

import hashlib
import io
import logging
import re
from urllib.parse import urlparse

from django.conf import settings

logger = logging.getLogger(__name__)

MAX_SIDE = 2000
MAX_BYTES = 15 * 1024 * 1024
CLOUDINARY_UPLOAD = re.compile(r'^(https?://res\.cloudinary\.com/[^/]+/image/upload/)(.+)$')
NOT_RASTER = ('.pdf', '.gif', '.svg')
RASTER = ('.jpg', '.jpeg', '.png', '.webp', '.heic', '.heif', '.avif', '.bmp', '.tif', '.tiff')
TRANSFORM_SEGMENT = re.compile(r'^[a-z]{1,3}_[^/]*$')  # e.g. w_800,c_fill (already transformed: leave it)
DENSITIES = (1, 2, 3)


def _path(url):
    return (url or '').split('?', 1)[0].lower()


def is_raster(url):
    return _path(url).endswith(RASTER)


def _cloudinary(url, width, height, crop):
    match = CLOUDINARY_UPLOAD.match(url)
    if not match:
        return None
    rest = match.group(2)
    # Cloudinary's image/upload is images only, and our media files there have no extension: transform everything
    # except PDFs, GIFs (animation) and SVGs, and URLs that already carry a transformation.
    if _path(rest).endswith(NOT_RASTER) or TRANSFORM_SEGMENT.match(rest.split('/', 1)[0]):
        return url
    parts = ['f_auto', 'q_auto', 'c_fill,g_auto' if crop and height else 'c_limit', f'w_{int(width)}']
    if height:
        parts.append(f'h_{int(height)}')
    return f'{match.group(1)}{",".join(parts)}/{rest}'


def _own_media_name(url):
    """The storage name of one of our media files (MEDIA_URL, absolute or relative), else None."""
    media = settings.MEDIA_URL or '/media/'
    path = urlparse(url).path if url.startswith(('http://', 'https://')) else url
    if url.startswith(('http://', 'https://')) and urlparse(url).hostname not in _own_hosts():
        return None
    return path[len(media):] if path.startswith(media) else None


def _own_hosts():
    hosts = {'localhost', '127.0.0.1'}
    site = urlparse(getattr(settings, 'SITE_URL', '') or '').hostname
    if site:
        hosts.add(site)
    return hosts


def _local_variant(url, width, height, crop):
    """A resized copy of one of our media files, made once with Pillow (no Cloudinary: local dev)."""
    if not getattr(settings, 'IMAGE_LOCAL_VARIANTS', False) or not is_raster(url):
        return None
    name = _own_media_name(url)
    if not name or name.startswith('variants/'):
        return None
    from django.core.files.base import ContentFile
    from django.core.files.storage import default_storage
    digest = hashlib.sha256(name.encode()).hexdigest()[:16]
    target = f'variants/{digest}-{int(width)}x{int(height or 0)}{"c" if crop else ""}.jpg'
    try:
        if not default_storage.exists(target):
            from PIL import Image, ImageOps
            with default_storage.open(name, 'rb') as fh:
                image = Image.open(fh)
                image.load()
            image = ImageOps.exif_transpose(image).convert('RGB')
            if crop and height:
                image = ImageOps.fit(image, (int(width), int(height)))
            else:
                image.thumbnail((int(width), int(height) if height else 10 ** 5))
            out = io.BytesIO()
            image.save(out, 'JPEG', quality=80, optimize=True, progressive=True)
            default_storage.save(target, ContentFile(out.getvalue()))
        return default_storage.url(target)
    except Exception as exc:  # a broken file never breaks the page
        logger.info('Local image variant failed: %s', exc.__class__.__name__)
        return None


def variant(url, width, height=None, crop=False):
    """``url`` at ``width`` (× ``height``) pixels; unchanged where no smaller copy can be made."""
    if not url or not width:
        return url
    found = _cloudinary(url, width, height, crop)
    if found is not None:
        return found
    return _local_variant(url, width, height, crop) or url


def responsive(url, width, height=None, crop=False, densities=DENSITIES):
    """(src, srcset): ``src`` at 1x and a width-descriptor ``srcset`` for 1x / 2x / 3x (or '' when every density
    would be the same file)."""
    src = variant(url, width, height, crop)
    entries, seen = [], set()
    for d in densities:
        u = variant(url, width * d, height * d if height else None, crop)
        if u in seen:
            continue
        seen.add(u)
        entries.append(f'{u} {int(width * d)}w')
    return src, (', '.join(entries) if len(entries) > 1 else '')


# ---------------------------------------------------------------------------
# Self-hosting outside images
# ---------------------------------------------------------------------------

def is_ours(url):
    """Our storage (Cloudinary account, our media) or a static file: nothing to copy."""
    if not url:
        return True
    if url.startswith('/'):
        return True
    host = urlparse(url).hostname or ''
    if host == 'res.cloudinary.com':
        cloud = ''
        try:
            cloud = (settings.CLOUDINARY_STORAGE or {}).get('CLOUD_NAME', '')
        except Exception:
            pass
        return not cloud or f'/{cloud}/' in url
    return host in _own_hosts()


def is_google_places_photo(url):
    from events.place_images import _expiring
    return _expiring(url)


def self_host(url, max_side=MAX_SIDE):
    """JPEG bytes of an outside image (fetched through Studio's SSRF guard, re-encoded without metadata), or None."""
    from events.place_images import _fetch, _load_image, reencode
    if not url or is_ours(url) or is_google_places_photo(url):
        return None
    got = _fetch(url, MAX_BYTES)
    if not got:
        return None
    image = _load_image(got[0])
    if image is None:
        return None
    if max(image.size) > max_side:
        image.thumbnail((max_side, max_side))
    return reencode(image)


def host_location(location):
    """Copy a location's outside ``image_url`` into ``Location.image``; drop Google Places photo links. Returns
    'hosted', 'dropped', 'failed' or 'skipped'."""
    from django.core.files.base import ContentFile
    url = location.image_url or ''
    if not url or location.image or is_ours(url):
        return 'skipped'
    if is_google_places_photo(url):
        location.image_url = None
        location.save(update_fields=['image_url'])
        from events import place_images
        if place_images.enabled():
            place_images.schedule(location.pk)
        return 'dropped'
    jpeg = self_host(url)
    if jpeg is None:
        return 'failed'
    digest = hashlib.sha256(jpeg).hexdigest()[:12]
    location.image.save(f'place-{location.pk}-{digest}.jpg', ContentFile(jpeg), save=False)
    location.image_source_url = location.image_source_url or url[:1024]
    location.image_url = None
    location.save(update_fields=['image', 'image_source_url', 'image_url'])
    return 'hosted'


def host_event_logo(event):
    """Copy an outside ``Event.logo_url`` into ``Event.logo_image`` (``logo_url`` keeps the source)."""
    from django.core.files.base import ContentFile
    url = event.logo_url or ''
    if not url or is_ours(url) or is_google_places_photo(url):
        return 'skipped'
    if event.logo_image and event.logo_source_url == url:
        return 'skipped'
    jpeg = self_host(url, max_side=1024)
    if jpeg is None:
        return 'failed'
    digest = hashlib.sha256(jpeg).hexdigest()[:12]
    event.logo_image.save(f'logo-{event.pk}-{digest}.jpg', ContentFile(jpeg), save=False)
    event.logo_source_url = url[:1024]
    event.save(update_fields=['logo_image', 'logo_source_url'])
    return 'hosted'


def event_logo_url(event):
    """The logo to show and ship: our copy when there is one for the current ``logo_url``, else ``logo_url``."""
    if event.logo_image and event.logo_source_url == (event.logo_url or ''):
        try:
            return event.logo_image.url
        except Exception:
            pass
    return event.logo_url or ''


def schedule_location(location_id):
    _later(lambda: _run_location(location_id))


def schedule_event_logo(event_id):
    _later(lambda: _run_logo(event_id))


def _run_location(location_id):
    from events.models import Location
    loc = Location.objects.filter(pk=location_id).first()
    if loc is not None:
        host_location(loc)


def _run_logo(event_id):
    from events.models import Event
    event = Event.objects.filter(pk=event_id).first()
    if event is not None:
        host_event_logo(event)


def _later(fn):
    if not getattr(settings, 'IMAGE_SELF_HOST_ON_SAVE', False):
        return
    import threading

    from django.db import close_old_connections, transaction

    def run():
        try:
            fn()
        except Exception:
            logger.exception('Self-hosting an image failed')
        finally:
            close_old_connections()
    transaction.on_commit(lambda: threading.Thread(target=run, daemon=True, name='self-host-image').start())
