"""Bring an existing event website into Studio (spec §9): capture its look and structure, copy its images and fonts
into the asset library, then let the AI rebuild a closely similar site.

Only for websites the organizer gave as the event's sources. Fetches use urllib with an SSRF guard: http(s) only,
public addresses only (checked again on every redirect), short timeouts and size caps.
"""

import ipaddress
import logging
import re
import socket
from collections import Counter
from urllib.parse import urljoin, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from bs4 import BeautifulSoup

from studio.models import StudioMessage, StudioRun

logger = logging.getLogger(__name__)

USER_AGENT = 'Mozilla/5.0 (compatible; AttndStudio/1.0; +https://attnd.app)'
MAX_HTML = 2 * 1024 * 1024
MAX_CSS = 500 * 1024
MAX_IMAGE = 8 * 1024 * 1024
MAX_FONT = 2 * 1024 * 1024
MAX_STYLESHEETS, MAX_IMAGES, MAX_FONTS, MAX_PAGES = 8, 24, 6, 4
TIMEOUT = 15


class SiteImportError(ValueError):
    pass


def _public_host(host):
    """True when every address the host resolves to is a public one."""
    try:
        infos = socket.getaddrinfo(host, None)
    except (socket.gaierror, UnicodeError):
        return False
    for info in infos:
        address = ipaddress.ip_address(info[4][0].split('%')[0])
        if (address.is_private or address.is_loopback or address.is_link_local or address.is_multicast
                or address.is_reserved or address.is_unspecified):
            return False
    return bool(infos)


def check_url(url):
    parts = urlparse(url)
    if parts.scheme not in ('http', 'https') or not parts.hostname:
        raise SiteImportError(f'{url} is not a web address.')
    if parts.port not in (None, 80, 443):
        raise SiteImportError(f'{url} uses an unusual port.')
    if not _public_host(parts.hostname):
        raise SiteImportError(f'{url} is not a public website.')
    return url


class _SafeRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        check_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_opener = build_opener(_SafeRedirects)


def fetch(url, limit):
    """(bytes, content_type, final_url). Raises SiteImportError for refused or failed fetches."""
    check_url(url)
    try:
        with _opener.open(Request(url, headers={'User-Agent': USER_AGENT, 'Accept': '*/*'}), timeout=TIMEOUT) as resp:
            data = resp.read(limit + 1)
            if len(data) > limit:
                raise SiteImportError(f'{url} is too big.')
            return data, (resp.headers.get('Content-Type') or '').split(';')[0].strip().lower(), resp.geturl()
    except SiteImportError:
        raise
    except Exception as exc:
        raise SiteImportError(f'Could not load {url}: {exc}') from exc


COLOR_RE = re.compile(r'#(?:[0-9a-fA-F]{6}|[0-9a-fA-F]{3})\b|rgba?\([^)]{5,40}\)')
FONT_FAMILY_RE = re.compile(r'font-family\s*:\s*([^;}{]+)', re.I)
FONT_FACE_URL_RE = re.compile(r'@font-face\s*{[^}]*?url\(\s*[\'"]?([^\'")]+)', re.I | re.S)
CSS_URL_RE = re.compile(r'url\(\s*[\'"]?([^\'")]+\.(?:png|jpe?g|gif|webp|svg)(?:\?[^\'")]*)?)', re.I)


def _norm_color(c):
    c = c.lower()
    if re.fullmatch(r'#[0-9a-f]{3}', c):
        c = '#' + ''.join(ch * 2 for ch in c[1:])
    return c


def outline(soup):
    """The page's structure, compactly: nav links, headings, buttons, sections."""
    out = {'title': (soup.title.get_text(strip=True) if soup.title else '')[:200],
           'nav': [], 'headings': [], 'buttons': [], 'sections': []}
    for a in soup.select('nav a, header a')[:40]:
        text = a.get_text(' ', strip=True)
        if text:
            out['nav'].append({'text': text[:60], 'href': a.get('href', '')[:200]})
    for h in soup.find_all(['h1', 'h2', 'h3'])[:60]:
        text = h.get_text(' ', strip=True)
        if text:
            out['headings'].append(f'{h.name}: {text[:160]}')
    for b in soup.select('a.button, a.btn, button, a[class*="button"], a[class*="btn"]')[:40]:
        text = b.get_text(' ', strip=True)
        if text:
            out['buttons'].append(text[:80])
    for s in soup.find_all(['section', 'header', 'footer', 'main'])[:30]:
        cls = ' '.join(s.get('class', []))[:80]
        first = s.find(['h1', 'h2', 'h3', 'p'])
        out['sections'].append({'tag': s.name, 'class': cls, 'starts': first.get_text(' ', strip=True)[:140] if first else ''})
    return out


def capture(url):
    """Everything about the site's look: {pages, css, colors, fonts, image_urls, font_urls, logo_url}."""
    html_bytes, ctype, final = fetch(url, MAX_HTML)
    if 'html' not in ctype:
        raise SiteImportError(f'{url} is not a web page.')
    html = html_bytes.decode('utf-8', 'replace')
    soup = BeautifulSoup(html, 'html.parser')
    css_parts = [style.get_text() for style in soup.find_all('style')]
    for link in soup.find_all('link', rel=lambda v: v and 'stylesheet' in v)[:MAX_STYLESHEETS]:
        href = link.get('href')
        if not href:
            continue
        try:
            data, _, _ = fetch(urljoin(final, href), MAX_CSS)
            css_parts.append(f'/* {href} */\n' + data.decode('utf-8', 'replace'))
        except SiteImportError as exc:
            logger.info('Studio import: stylesheet skipped: %s', exc)
    css = '\n'.join(css_parts)
    inline = ' '.join(tag.get('style', '') for tag in soup.find_all(style=True))
    colors = Counter(_norm_color(c) for c in COLOR_RE.findall(css + inline))
    fonts = Counter(f.strip().split(',')[0].strip(' \'"') for f in FONT_FAMILY_RE.findall(css))
    font_urls = list(dict.fromkeys(urljoin(final, u) for u in FONT_FACE_URL_RE.findall(css)
                                   if re.search(r'\.(woff2?|ttf|otf)(\?|$)', u, re.I)))[:MAX_FONTS]
    images, logo = [], None
    og = soup.find('meta', property='og:image')
    if og and og.get('content'):
        images.append(urljoin(final, og['content']))
    for img in soup.find_all('img'):
        src = img.get('src') or (img.get('srcset') or '').split(' ')[0] or img.get('data-src')
        if not src or src.startswith('data:'):
            continue
        absolute = urljoin(final, src)
        images.append(absolute)
        hint = ' '.join([img.get('alt', ''), ' '.join(img.get('class', [])), src]).lower()
        if logo is None and 'logo' in hint:
            logo = absolute
    images += [urljoin(final, u) for u in CSS_URL_RE.findall(css)]
    images = list(dict.fromkeys(images))[:MAX_IMAGES]
    pages = [{'url': final, 'outline': outline(soup),
              'html': re.sub(r'\s+', ' ', str(soup.body or soup))[:40_000]}]
    host = urlparse(final).netloc
    seen = {final}
    for link in [a.get('href', '') for a in soup.select('nav a, header a')]:
        target = urljoin(final, link).split('#')[0]
        if len(pages) > MAX_PAGES or target in seen or urlparse(target).netloc != host:
            continue
        seen.add(target)
        try:
            data, ctype, _ = fetch(target, MAX_HTML)
        except SiteImportError:
            continue
        if 'html' in ctype:
            sub = BeautifulSoup(data.decode('utf-8', 'replace'), 'html.parser')
            pages.append({'url': target, 'outline': outline(sub)})
    return {'url': final, 'pages': pages, 'css': css[:60_000], 'colors': [c for c, _ in colors.most_common(12)],
            'fonts': [f for f, _ in fonts.most_common(6) if f], 'image_urls': images, 'font_urls': font_urls,
            'logo_url': logo}


def copy_assets(event, look, user):
    """Copy the captured images and fonts into the library. Returns {original url: asset}."""
    from studio.assets import AssetError, create_asset
    copied = {}
    for url in look['image_urls'] + look['font_urls']:
        limit = MAX_FONT if url in look['font_urls'] else MAX_IMAGE
        try:
            data, _, _ = fetch(url, limit)
            name = urlparse(url).path.rsplit('/', 1)[-1] or 'image'
            tags = ['imported'] + (['logo'] if url == look.get('logo_url') else [])
            copied[url] = create_asset(event, data, name, user=user, origin_url=url, tags=tags)
        except (SiteImportError, AssetError) as exc:
            logger.info('Studio import: asset skipped: %s', exc)
    return copied


def allowed_url(event, url):
    """Only websites the organizer gave as this event's sources (same host)."""
    host = urlparse(url).netloc.lower().removeprefix('www.')
    return any(urlparse(u).netloc.lower().removeprefix('www.') == host for u in (event.source_urls or []))


def start_import(site, url, user, note=''):
    """Queue the import run (it captures the site in the background thread, then asks the AI)."""
    from studio import agent
    from studio.prompts import IMPORT_PROMPT
    if not allowed_url(site.event, url):
        raise SiteImportError('Only the event\'s own source websites can be brought in. Add it under the event\'s '
                           'website settings first.')
    prompt = IMPORT_PROMPT + (f'\n\nOrganizer notes: {note[:2000]}' if note else '')
    run = StudioRun.objects.create(site=site, created_by=user, kind=StudioRun.KIND_IMPORT, prompt=prompt,
                                   context={'import_url': url})
    StudioMessage.objects.create(site=site, role=StudioMessage.ROLE_USER, created_by=user, run=run,
                                 text=f'Bring {url} into Studio' + (f': {note}' if note else ''))
    agent.start(run)
    return run


def prepare_run(run):
    """Before the AI runs (called by the agent for import runs): capture the site and copy its assets."""
    url = (run.context or {}).get('import_url')
    if not url or (run.context or {}).get('captured'):
        return
    look = capture(url)
    copied = copy_assets(run.site.event, look, run.created_by)
    run.context = {
        'import_url': url, 'captured': True, 'pages': look['pages'], 'css': look['css'], 'colors': look['colors'],
        'fonts': look['fonts'],
        'copied_assets': [{'from': u, 'path': a.site_path, 'id': str(a.id), 'kind': a.kind} for u, a in copied.items()],
        'logo_asset_id': str(copied[look['logo_url']].id) if look.get('logo_url') in copied else None,
    }
    run.save(update_fields=['context'])

