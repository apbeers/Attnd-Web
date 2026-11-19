"""The per-event asset library: validation (types sniffed from the bytes), limits, saving and AI descriptions."""

import hashlib
import io
import logging
import re
import threading

from django.core.files.base import ContentFile
from django.db import close_old_connections, connection, transaction
from django.db.models import Sum

from studio.models import StudioAsset

logger = logging.getLogger(__name__)

MB = 1024 * 1024
LIMITS = {StudioAsset.KIND_IMAGE: 10 * MB, StudioAsset.KIND_SVG: 1 * MB, StudioAsset.KIND_PDF: 20 * MB,
          StudioAsset.KIND_FONT: 5 * MB}
MAX_ASSETS = 200
MAX_TOTAL = 300 * MB
EXTENSIONS = {'image/png': 'png', 'image/jpeg': 'jpg', 'image/gif': 'gif', 'image/webp': 'webp',
              'image/svg+xml': 'svg', 'application/pdf': 'pdf', 'font/woff': 'woff', 'font/woff2': 'woff2',
              'font/ttf': 'ttf', 'font/otf': 'otf'}


class AssetError(ValueError):
    pass


def sniff(data: bytes):
    """(kind, content_type) from the file's first bytes, or None. Never trusts a filename or a browser's type."""
    head = data[:512]
    if head.startswith(b'\x89PNG\r\n\x1a\n'):
        return StudioAsset.KIND_IMAGE, 'image/png'
    if head.startswith(b'\xff\xd8\xff'):
        return StudioAsset.KIND_IMAGE, 'image/jpeg'
    if head.startswith((b'GIF87a', b'GIF89a')):
        return StudioAsset.KIND_IMAGE, 'image/gif'
    if head[:4] == b'RIFF' and head[8:12] == b'WEBP':
        return StudioAsset.KIND_IMAGE, 'image/webp'
    if head.startswith(b'%PDF-'):
        return StudioAsset.KIND_PDF, 'application/pdf'
    if head.startswith(b'wOFF'):
        return StudioAsset.KIND_FONT, 'font/woff'
    if head.startswith(b'wOF2'):
        return StudioAsset.KIND_FONT, 'font/woff2'
    if head.startswith((b'\x00\x01\x00\x00', b'true')):
        return StudioAsset.KIND_FONT, 'font/ttf'
    if head.startswith(b'OTTO'):
        return StudioAsset.KIND_FONT, 'font/otf'
    text = head.lstrip(b'\xef\xbb\xbf \t\r\n').lower()
    if (text.startswith(b'<svg') or (text.startswith((b'<?xml', b'<!--', b'<!doctype svg')) and b'<svg' in data[:4096].lower())):
        return StudioAsset.KIND_SVG, 'image/svg+xml'
    return None


def safe_name(filename, content_type):
    stem = (filename or 'file').rsplit('/', 1)[-1].rsplit('\\', 1)[-1]
    stem = stem.rsplit('.', 1)[0] if '.' in stem else stem
    stem = re.sub(r'[^a-zA-Z0-9_-]+', '-', stem).strip('-').lower()[:60] or 'file'
    return f'{stem}.{EXTENSIONS[content_type]}'


def check_library(event, size):
    used = StudioAsset.objects.filter(event=event).aggregate(total=Sum('size'))['total'] or 0
    count = StudioAsset.objects.filter(event=event).count()
    if count >= MAX_ASSETS:
        raise AssetError(f'The library is full ({MAX_ASSETS} files). Delete some first.')
    if used + size > MAX_TOTAL:
        raise AssetError('The library is full (300 MB). Delete some files first.')


def create_asset(event, data: bytes, filename, user=None, generated=False, origin_url='', describe=True,
                 description='', tags=None, alt='', dedupe=False):
    """Validate and store one file; returns the StudioAsset. Raises AssetError with a message for the organizer.

    dedupe=True (imports): an identical file (same SHA-256) already in this event's library is returned instead of
    a copy; the new tags are added to it."""
    digest = hashlib.sha256(data).hexdigest()
    if dedupe:
        existing = StudioAsset.objects.filter(event=event, sha256=digest).first()
        if existing is not None:
            merged = list(dict.fromkeys([*existing.tags, *(tags or [])]))[:12]
            if merged != existing.tags:
                existing.tags = merged
                existing.save(update_fields=['tags'])
            return existing
    found = sniff(data)
    if not found:
        raise AssetError(f'{filename or "That file"}: only PNG, JPEG, GIF, WebP, SVG, PDF and font files (WOFF, WOFF2, '
                         f'TTF, OTF) can be added.')
    kind, content_type = found
    if len(data) > LIMITS[kind]:
        raise AssetError(f'{filename or "That file"} is too big: {kind.upper() if kind != "image" else "images"} '
                         f'can be up to {LIMITS[kind] // MB} MB.')
    check_library(event, len(data))
    width = height = None
    if kind == StudioAsset.KIND_IMAGE:
        try:
            from PIL import Image
            with Image.open(io.BytesIO(data)) as img:
                width, height = img.size
                img.verify()
        except Exception as exc:
            raise AssetError(f'{filename or "That image"} could not be read as an image.') from exc
    asset = StudioAsset(event=event, name=safe_name(filename, content_type), kind=kind, content_type=content_type,
                        size=len(data), width=width, height=height, created_by=user, generated=generated,
                        origin_url=origin_url[:1024], description=description[:1000], tags=list(tags or [])[:12],
                        alt=alt[:300], sha256=digest)
    asset.file.save(asset.name, ContentFile(data), save=False)
    asset.save()
    if describe and kind in (StudioAsset.KIND_IMAGE, StudioAsset.KIND_SVG) and not description:
        transaction.on_commit(lambda: start_describe(asset.pk))
    return asset


def read_bytes(asset):
    with asset.file.open('rb') as fh:
        return fh.read()


def delete_asset(asset):
    try:
        asset.file.delete(save=False)
    except Exception:
        logger.warning('Could not delete the stored file of asset %s', asset.pk, exc_info=True)
    asset.delete()


def asset_brief(asset):
    """How the AI sees an asset."""
    dims = f'{asset.width}x{asset.height}' if asset.width else ''
    return {'id': str(asset.id), 'path': asset.site_path, 'kind': asset.kind, 'type': asset.content_type,
            'size': dims, 'description': asset.description, 'alt': asset.alt, 'tags': asset.tags,
            'colors': asset.colors, 'generated': asset.generated}


# ---------------------------------------------------------------------------
# AI descriptions (vision), in a background thread after upload
# ---------------------------------------------------------------------------

DESCRIBE_SCHEMA = {
    'type': 'object',
    'properties': {
        'description': {'type': 'string', 'description': 'One or two sentences: what it shows, style, any text in it'},
        'alt': {'type': 'string', 'description': 'Alt text for a web page, under 140 characters'},
        'tags': {'type': 'array', 'items': {'type': 'string'},
                 'description': '3-8 lower-case tags, e.g. logo, sponsor, photo, crowd, flyer, artwork, map, icon'},
        'colors': {'type': 'array', 'items': {'type': 'string'}, 'description': 'Up to 5 main colours as #rrggbb'},
    },
    'required': ['description', 'alt', 'tags', 'colors'],
    'additionalProperties': False,
}


def start_describe(asset_id):
    def main():
        close_old_connections()
        try:
            describe(asset_id)
        except Exception:
            logger.exception('Describing asset %s failed', asset_id)
        finally:
            connection.close()
    threading.Thread(target=main, daemon=True, name=f'studio-describe-{asset_id}').start()


def describe(asset_id):
    """Ask the vision model for a description, alt text, tags and colours (skipped without an OpenAI key or budget)."""
    import base64
    import os

    from studio import ai
    asset = StudioAsset.objects.select_related('event').filter(pk=asset_id).first()
    if asset is None or not os.getenv('OPENAI_API_KEY') or not ai.budget_left(asset.event):
        return
    data = read_bytes(asset)
    if asset.kind == StudioAsset.KIND_SVG:
        part = {'type': 'text', 'text': 'SVG source:\n' + data[:20000].decode('utf-8', 'replace')}
    else:
        part = {'type': 'image_url', 'image_url': {
            'url': f'data:{asset.content_type};base64,{base64.b64encode(data).decode()}', 'detail': 'low'}}
    result, usage = ai.describe_image(part)
    ai.record_usage(asset.event, None, 'describe', 'openai', usage['model'], usage['input'], usage['output'])
    asset.description = str(result.get('description', ''))[:1000]
    asset.alt = str(result.get('alt', ''))[:300]
    asset.tags = [str(t).lower()[:32] for t in result.get('tags', [])][:12]
    asset.colors = [c for c in result.get('colors', []) if re.fullmatch(r'#[0-9a-fA-F]{6}', str(c))][:5]
    asset.save(update_fields=['description', 'alt', 'tags', 'colors'])


def parse_tags(text):
    return [t.strip().lower()[:32] for t in (text or '').split(',') if t.strip()][:12]

