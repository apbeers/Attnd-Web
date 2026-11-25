"""Console components as MCP Apps (docs/mcp_plan.md): the same templates the console and the chat draw, made to
work inside a chat client's sandboxed iframe, which has no cookies and no Attnd origin (Nov 25, 2026, tested from a
Claude chat on dev):

* the event's time zone is active while the data is loaded and drawn (times read as the organizer set them);
* images that need a console sign-in (the join and preview QR codes) are drawn inline as ``data:`` PNGs;
* every link, image and form address is absolute (``SITE_URL``), and links open in a new tab (the app asks the
  host to open them: ``ui/open-link``);
* the event map, drawn by console.js on a page, becomes a static picture: OpenStreetMap tiles with the pins placed
  by the server (the tile host is in the resource's CSP, ``RESOURCE_DOMAINS``).
"""

import base64
import json
import math
import re
from contextlib import nullcontext
from html import escape
from urllib.parse import urlsplit

from django.conf import settings
from django.utils import timezone

TILE_HOST = 'https://tile.openstreetmap.org'


def resource_domains():
    """Origins the app's HTML loads images from (MCP Apps `_meta.ui.csp.resourceDomains`)."""
    site = urlsplit(settings.SITE_URL)
    out = [f'{site.scheme}://{site.netloc}', TILE_HOST, 'https://res.cloudinary.com', 'https://upload.wikimedia.org']
    return list(dict.fromkeys(out))


def _event_tz(event_id):
    from events.models import Event
    from scrape.diff import tz_or_utc
    tz = Event.objects.filter(pk=event_id).values_list('timezone', flat=True).first() if event_id else None
    return timezone.override(tz_or_utc(tz)) if tz else nullcontext()


def render(user, name, items):
    """[(ids, data, html)] for one component and its id sets, as the person; ValueError with a message."""
    from agent.components import _load
    from console import components as C
    out = []
    for ids in items or [{}]:
        ids = ids if isinstance(ids, dict) else {}
        with _event_tz(ids.get('event_id')):
            for found_ids, data in _load(user, name, [ids]):
                event_id = found_ids.get('event_id') or data.get('event_id')
                with _event_tz(event_id) if event_id != ids.get('event_id') else nullcontext():
                    html = C.render(name, data, None, surface='chat', uid=C.new_uid())
                out.append((found_ids, data, for_app(html, user)))
    return out


# --- HTML for the iframe ------------------------------------------------------------------------------------------

QR_SRC = re.compile(r'(src|href)="(?:/console/events/(\d+)/(qr|preview)\.png)(?:\?[^"]*)?"')
LOCAL_URL = re.compile(r'\b(href|src|action|formaction|poster)="/(?!/)')
LINK = re.compile(r'<a\b(?![^>]*\btarget=)')
EVENT_MAP = re.compile(r'<div class="pin-map event-map" data-event-map data-pins="([^"]*)" data-center="([^"]*)" '
                       r'data-zoom="([^"]*)"[^>]*></div>')


def _qr_data(user, event_id, kind, cache):
    key = (event_id, kind)
    if key not in cache:
        from attnd.links import join_url, qr_png
        from events.models import Event
        event = Event.objects.filter(pk=event_id).first()
        if event is None or not event.can_edit(user):
            cache[key] = ''
        else:
            if kind == 'preview':
                from events import preview
                link = preview.link(event, user)
            else:
                link = join_url(event)
            cache[key] = 'data:image/png;base64,' + base64.b64encode(qr_png(link, box_size=8)).decode()
    return cache[key]


def for_app(html, user):
    cache = {}

    def qr(m):
        attr, event_id, kind = m.group(1), int(m.group(2)), m.group(3)
        if attr == 'href':  # a "Download QR" link: the console's file (signed in), opened in a new tab
            return m.group(0)
        data = _qr_data(user, event_id, kind, cache)
        return f'src="{data}"' if data else m.group(0)
    html = QR_SRC.sub(qr, html)
    html = EVENT_MAP.sub(lambda m: static_map(m.group(1), m.group(2), m.group(3)), html)
    html = LOCAL_URL.sub(lambda m: f'{m.group(1)}="{settings.SITE_URL.rstrip("/")}/', html)
    return LINK.sub('<a target="_blank" rel="noopener"', html)


# --- A static map of the pins (no script, no API key) -------------------------------------------------------------

TILE, WIDTH, HEIGHT = 256, 640, 300


def _px(lat, lng, zoom):
    lat = max(-85.0, min(85.0, lat))
    n = TILE * 2 ** zoom
    x = (lng + 180) / 360 * n
    s = math.sin(math.radians(lat))
    y = (0.5 - math.log((1 + s) / (1 - s)) / (4 * math.pi)) * n
    return x, y


def static_map(pins_json, center_json, zoom_text):
    import html as html_lib
    try:
        pins = json.loads(html_lib.unescape(pins_json))
        center = json.loads(html_lib.unescape(center_json))
    except ValueError:
        return ''
    pins = [p for p in pins if isinstance(p, dict) and isinstance(p.get('lat'), (int, float))
            and isinstance(p.get('lng'), (int, float))]
    if not pins:
        return ''
    zoom = 18
    while zoom > 2:  # the closest zoom that keeps every pin inside the frame, with a margin
        xs, ys = zip(*(_px(p['lat'], p['lng'], zoom) for p in pins))
        if max(xs) - min(xs) <= WIDTH - 60 and max(ys) - min(ys) <= HEIGHT - 60:
            break
        zoom -= 1
    xs, ys = zip(*(_px(p['lat'], p['lng'], zoom) for p in pins))
    cx, cy = (max(xs) + min(xs)) / 2, (max(ys) + min(ys)) / 2
    left, top = cx - WIDTH / 2, cy - HEIGHT / 2
    n = 2 ** zoom
    # One SVG with a fixed viewBox: it scales to whatever width the client's frame has, pins stay on the tiles.
    tiles = []
    for tx in range(int(left // TILE), int((left + WIDTH) // TILE) + 1):
        for ty in range(int(top // TILE), int((top + HEIGHT) // TILE) + 1):
            if 0 <= ty < n:
                tiles.append(f'<image href="{TILE_HOST}/{zoom}/{tx % n}/{ty}.png" x="{tx * TILE - left:.0f}" '
                             f'y="{ty * TILE - top:.0f}" width="{TILE}" height="{TILE}"/>')
    dots = []
    for p in pins:
        x, y = _px(p['lat'], p['lng'], zoom)
        dots.append(f'<circle cx="{x - left:.0f}" cy="{y - top:.0f}" r="7" fill="#431f86" stroke="#fff" '
                    f'stroke-width="2.5"><title>{escape(str(p.get("name", "")))}</title></circle>')
    return (f'<svg class="attnd-static-map" viewBox="0 0 {WIDTH} {HEIGHT}" role="img" '
            f'aria-label="Map of {len(pins)} locations" xmlns="http://www.w3.org/2000/svg" '
            'style="display:block;width:100%;height:auto;border-radius:12px;background:#e8e4f0">'
            f'{"".join(tiles)}{"".join(dots)}'
            f'<rect x="{WIDTH - 172}" y="{HEIGHT - 18}" width="168" height="15" rx="4" fill="#fff" fill-opacity=".85"/>'
            f'<text x="{WIDTH - 8}" y="{HEIGHT - 7}" text-anchor="end" font-size="10" '
            'font-family="system-ui,sans-serif" fill="#333">© OpenStreetMap contributors</text></svg>')
