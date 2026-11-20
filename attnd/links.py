"""Links the apps open, and QR codes for them.

Production links are `https://attnd.app/...`. The dev server (`SITE_URL` https://dev.attnd.app) makes its own
links `https://dev.attnd.app/...` (Nov 20, 2026, owner): iOS opens both domains as Universal Links (its
associated domains list both) and the dev server serves the same AASA, so a dev event's QR / share link no longer
lands on production's 404 when it opens in a browser. Any other `SITE_URL` (local, review apps) keeps attnd.app.
Android App Links are verified for attnd.app only (dev.attnd.app links open in the browser there).
"""
import base64
import io
from urllib.parse import urlsplit

import qrcode
import qrcode.image.svg
from django.conf import settings

PRODUCTION_ORIGIN = 'https://attnd.app'
DEV_ORIGIN = 'https://dev.attnd.app'


def app_link_origin() -> str:
    """The origin of join and shared-plan links: dev.attnd.app on the dev server, attnd.app everywhere else."""
    site = urlsplit(getattr(settings, 'SITE_URL', '') or '')
    return DEV_ORIGIN if site.scheme == 'https' and site.hostname == 'dev.attnd.app' else PRODUCTION_ORIGIN


def join_url(event_or_code) -> str:
    """`https://attnd.app/join/<code>/` (dev: dev.attnd.app): what every join QR code and poster encodes."""
    code = getattr(event_or_code, 'event_code', event_or_code)
    return f'{app_link_origin()}/join/{code}/'


def shared_plan_url(event_code: str, payload: str) -> str:
    """API-015 shared plan link `https://attnd.app/p/<code>/<payload>` (no trailing slash, as the apps write it)."""
    return f'{app_link_origin()}/p/{event_code}/{payload.strip().rstrip("=")}'


def qr_png(text: str, box_size: int = 8, border: int = 2) -> bytes:
    buf = io.BytesIO()
    qrcode.make(text, box_size=box_size, border=border).save(buf, format='PNG')
    return buf.getvalue()


def qr_png_data_url(text: str, box_size: int = 8) -> str:
    return 'data:image/png;base64,' + base64.b64encode(qr_png(text, box_size)).decode()


def qr_svg(text: str, box_size: int = 10, border: int = 2, error_correction=None) -> str:
    kwargs = {'error_correction': error_correction} if error_correction is not None else {}
    image = qrcode.make(text, image_factory=qrcode.image.svg.SvgPathImage, box_size=box_size, border=border, **kwargs)
    return image.to_string(encoding='unicode')
