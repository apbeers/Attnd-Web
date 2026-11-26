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


def ios_open_url(path: str) -> str:
    """The link an iPhone taps to open `path` (/join/<code>/…) in the app from one of our own pages.

    iOS doesn't open a Universal Link that points at the domain Safari is showing, and the app's own scheme
    (attnd://) makes Safari show "the address is invalid" when the app isn't installed (owner, Nov 26, 2026). So
    production uses its sibling host www.attnd.app, which the app also claims (applinks:www.attnd.app): with the app
    installed it opens at once; without it the browser loads www, which redirects to attnd.app/<path> (the join page
    with the App Clip card). The dev server has no sibling host: its link is the join page itself."""
    origin = app_link_origin()
    return ('https://www.attnd.app' if origin == PRODUCTION_ORIGIN else origin) + path


def open_in_app_links(event_or_code) -> dict:
    """The "Open in Attnd" button on a phone (the console's QR card and the home page's cards: people can't scan
    their own screen). iPhones get `ios_open_url` (a Universal Link on www.attnd.app, never an error alert).
    Android gets Chrome's intent URL for the join link with the app's package and the join link as the browser
    fallback (`intent://<host>/join/<code>/#Intent;scheme=https;package=…;S.browser_fallback_url=…;end`)."""
    from urllib.parse import quote
    code = getattr(event_or_code, 'event_code', event_or_code)
    fallback = join_url(code)
    host = urlsplit(fallback).netloc
    package = getattr(settings, 'ANDROID_PACKAGE_NAME', '') or 'com.arcu.attnd'
    return {
        'ios': ios_open_url(f'/join/{quote(code, safe="")}/'),
        'android': (f'intent://{host}/join/{quote(code, safe="")}/#Intent;scheme=https;package={package};'
                    f'S.browser_fallback_url={quote(fallback, safe="")};end'),
        'fallback': fallback,
    }


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
