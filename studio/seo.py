"""What every hosted page gets around the organizer's HTML (batch 7 §6): SEO tags (schema.org Event JSON-LD from the
event's real data on the home page, Open Graph / Twitter tags with a share image per page, hreflang links between
translations), the "Get the app" banner with the join QR, and the cookie-free analytics beacon. Pages that already
have a tag keep theirs. Previews get the banner (so it can be seen) but no beacon."""

import html
import json
import re
from zoneinfo import ZoneInfo

from attnd.links import join_url
from studio import hosting, quality

_LD_RE = re.compile(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', re.I | re.S)


def _abs(origin_base, src):
    if not src:
        return ''
    if src.startswith(('http://', 'https://')):
        return src
    if src.startswith('//'):
        return 'https:' + src
    return origin_base + src.lstrip('./')


def event_json_ld(site, url):
    """schema.org Event from the event as it is now (never what the page text says)."""
    event = site.event
    if not event.start_time:
        return None
    tz = ZoneInfo(event.timezone or 'UTC')
    data = {'@context': 'https://schema.org', '@type': 'Event', 'name': event.name, 'url': url,
            'startDate': event.start_time.astimezone(tz).isoformat(),
            'eventAttendanceMode': 'https://schema.org/OfflineEventAttendanceMode',
            'eventStatus': 'https://schema.org/EventScheduled'}
    if event.end_time:
        data['endDate'] = event.end_time.astimezone(tz).isoformat()
    if event.description:
        data['description'] = re.sub(r'\s+', ' ', event.description).strip()[:500]
    place = {'@type': 'Place', 'name': (event.address or event.name).split(',')[0][:120]}
    if event.address:
        place['address'] = event.address[:300]
    if event.latitude is not None and event.longitude is not None and (event.latitude or event.longitude):
        place['geo'] = {'@type': 'GeoCoordinates', 'latitude': float(event.latitude),
                        'longitude': float(event.longitude)}
    data['location'] = place
    image = _event_image(event)
    if image:
        data['image'] = [image]
    return data


def _event_image(event):
    from events import content
    try:
        ev = content.event_v2(event)
    except Exception:
        return ''
    for key in ('flyer_url', 'logo_url'):
        url = ev.get(key) or ''
        if url.startswith('https://') and not url.lower().endswith('.pdf'):
            return url
    return ''


def _ld_script(data):
    text = json.dumps(data, ensure_ascii=False).replace('</', '<\\/')
    return f'<script type="application/ld+json">{text}</script>'


def head_tags(site, files, path, page_url, origin_base):
    """The extra <head> tags for one page."""
    page = quality.parse(files.get(path, ''))
    tags = []
    title = page.title.strip() or site.event.name
    description = page.metas.get('description') or re.sub(r'\s+', ' ', site.event.description or '').strip()[:200]
    if 'og:title' not in page.metas:
        tags.append(f'<meta property="og:title" content="{html.escape(title)}">')
    if 'og:description' not in page.metas and description:
        tags.append(f'<meta property="og:description" content="{html.escape(description)}">')
    if 'og:type' not in page.metas:
        tags.append('<meta property="og:type" content="website">')
    if 'og:url' not in page.metas and page_url:
        tags.append(f'<meta property="og:url" content="{html.escape(page_url)}">')
    if 'og:image' not in page.metas:
        image = next((_abs(origin_base, img.get('src', '')) for img in page.images
                      if img.get('src') and not img['src'].lower().endswith('.svg')), '') or _event_image(site.event)
        if image:
            tags.append(f'<meta property="og:image" content="{html.escape(image)}">')
            if 'twitter:card' not in page.metas:
                tags.append('<meta name="twitter:card" content="summary_large_image">')
    if path == 'index.html' and not any('"Event"' in m for m in _LD_RE.findall(files.get(path, ''))):
        data = event_json_ld(site, page_url)
        if data:
            tags.append(_ld_script(data))
    tags.extend(hreflang(files, path, origin_base))
    return ''.join(tags)


def hreflang(files, path, origin_base):
    """<link rel=alternate hreflang> between a page and its translations (es/index.html …)."""
    lang, _, rest = path.partition('/')
    source = rest if lang in quality.LANGS and rest else path
    langs = [code for code in quality.LANGS if f'{code}/{source}' in files]
    if not langs:
        return []

    def url(p):
        return origin_base + ('' if p == 'index.html' else p.removesuffix('index.html'))
    out = [f'<link rel="alternate" hreflang="x-default" href="{html.escape(url(source))}">']
    out += [f'<link rel="alternate" hreflang="{c}" href="{html.escape(url(f"{c}/{source}"))}">' for c in langs]
    return out


BANNER_STYLE = (
    '.attnd-app{position:fixed;z-index:2147483000;right:12px;bottom:12px;max-width:min(360px,calc(100vw - 24px));'
    'display:flex;gap:10px;align-items:center;padding:10px 12px;border-radius:14px;background:#fff;color:#1d1530;'
    'box-shadow:0 6px 24px rgba(30,10,70,.22);font:14px/1.35 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,'
    'sans-serif}.attnd-app img{width:72px;height:72px;flex:none}.attnd-app p{margin:0}.attnd-app strong{display:block}'
    '.attnd-app a.attnd-app__open{display:inline-block;margin-top:6px;padding:6px 12px;border-radius:999px;'
    'background:#431f86;color:#fff;text-decoration:none;font-weight:600}.attnd-app button{position:absolute;top:4px;'
    'right:6px;border:0;background:none;font-size:18px;line-height:1;color:#6b6480;cursor:pointer;padding:4px}'
    '@media (max-width:640px){.attnd-app img{display:none}}'
    '@media (prefers-color-scheme:dark){.attnd-app{background:#221a33;color:#f2eefa}.attnd-app button{color:#bdb4d0}}'
    '@media print{.attnd-app{display:none}}')


def app_banner(site, base):
    """"Get the app" with the join QR (desktop) / Open in Attnd (phones). Hidden for the tab once closed
    (sessionStorage, no cookie). Sites that draw their own <attnd-app-badge> still get it: it is the platform's."""
    event = site.event
    link = html.escape(join_url(event))
    return (f'<style>{BANNER_STYLE}</style>'
            f'<aside class="attnd-app" data-attnd-app-banner aria-label="Get the Attnd app for this event">'
            f'<img src="_attnd/qr.svg" alt="QR code: open {html.escape(event.name)} in Attnd" width="72" height="72">'
            f'<p><strong>Get the app for this event</strong>Schedule, map and live updates on your phone · code '
            f'<b>{html.escape(event.event_code)}</b><br><a class="attnd-app__open" href="{link}" data-attnd-open-app '
            f'rel="noopener">Open in Attnd</a></p>'
            f'<button type="button" aria-label="Close" data-attnd-app-close>×</button></aside>'
            "<script>(function(){var b=document.querySelector('[data-attnd-app-banner]');if(!b)return;"
            "try{if(sessionStorage.getItem('attnd.app.closed'))b.remove();}catch(e){}"
            "b.querySelector('[data-attnd-app-close]').addEventListener('click',function(){b.remove();"
            "try{sessionStorage.setItem('attnd.app.closed','1');}catch(e){}});})();</script>")


def beacon(path):
    """Cookie-free counts (studio.views_sites hit): a page view once per load, taps on Open in Attnd / store links.
    Sends the page path and the referring host only."""
    page = json.dumps(path[:120])
    return ("<script>(function(){function s(k){try{var r='';try{var u=new URL(document.referrer);"
            "if(u.host!==location.host)r=u.host;}catch(e){}"
            f"navigator.sendBeacon('_attnd/hit',JSON.stringify({{k:k,p:{page},r:r}}));}}catch(e){{}}}}"
            "s('view');document.addEventListener('click',function(e){var a=e.target.closest&&e.target.closest('a');"
            "if(!a)return;var h=a.href||'';if(a.hasAttribute('data-attnd-open-app')||/\\/join\\//.test(h))s('open_app');"
            "else if(/apps\\.apple\\.com|play\\.google\\.com/.test(h))s('get_app');},true);})();</script>")


def site_sitemap(site, files):
    """/<slug>/sitemap.xml: every page of the live version (not 404.html), translations included."""
    from xml.sax.saxutils import escape
    origin = hosting.sites_origin() + f'/{site.slug}/'
    rows = []
    for path in sorted(p for p in files if p.endswith('.html') and not p.endswith('404.html')):
        loc = origin + ('' if path == 'index.html' else path.removesuffix('index.html'))
        rows.append(f'<url><loc>{escape(loc)}</loc>'
                    + (f'<lastmod>{site.published_at.date().isoformat()}</lastmod>' if site.published_at else '')
                    + '</url>')
    return ('<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            + ''.join(rows) + '</urlset>')


def site_robots(site):
    origin = hosting.sites_origin()
    return (f'User-agent: *\nAllow: /{site.slug}/\nDisallow: /{site.slug}/_preview/\nDisallow: /{site.slug}/_attnd/\n'
            f'Sitemap: {origin}/{site.slug}/sitemap.xml\n')


def demo_ribbon():
    return ('<div data-attnd-demo style="position:sticky;top:0;z-index:2147482000;padding:6px 12px;text-align:center;'
            'background:#fff4d6;color:#5a4300;font:600 13px/1.4 sans-serif">Demo event · made with Attnd</div>')

