"""Links the apps open, and QR codes for them.

App Links (Android) and Universal Links (iOS) are verified for attnd.app only, and both apps' QR scanners accept
only attnd.app links. So join and shared-plan links always use APP_LINK_ORIGIN, never `SITE_URL` (dev's
dev.attnd.app links would not open the apps; a dev build looks the code up on the dev server either way).
"""
import base64
import io

import qrcode
import qrcode.image.svg

APP_LINK_ORIGIN = 'https://attnd.app'


def join_url(event_or_code) -> str:
    """`https://attnd.app/join/<code>/`: what every join QR code and poster encodes."""
    code = getattr(event_or_code, 'event_code', event_or_code)
    return f'{APP_LINK_ORIGIN}/join/{code}/'


def shared_plan_url(event_code: str, payload: str) -> str:
    """API-015 shared plan link `https://attnd.app/p/<code>/<payload>` (no trailing slash, as the apps write it)."""
    return f'{APP_LINK_ORIGIN}/p/{event_code}/{payload.strip().rstrip("=")}'


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
