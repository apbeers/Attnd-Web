"""Bring an existing event website into Studio (spec §9): crawl the whole site (≤ 25 pages on the same host), copy
every image (img, srcset, picture, lazy-load attributes, CSS backgrounds, og:image, icons, logos, sponsor logos),
self-hosted font and PDF (flyer / guide / map) into the asset library, capture each page's structure, the CSS,
colours and fonts, then let the AI rebuild the whole multi-page site with those local copies.

Only for websites the organizer gave as the event's sources. Fetches use urllib with an SSRF guard: http(s) only,
public addresses only (checked again on every redirect), short timeouts, size caps per file, and per run a time cap
(`STUDIO_IMPORT_SECONDS`) and a byte cap (`STUDIO_IMPORT_MAX_MB`). Copies go through `assets.create_asset` (type
sniffing, size limits, the per-event library budget) and are deduplicated by SHA-256. Pages are fetched from the
seeds' hosts only; images, fonts and PDFs may come from any public host (CDNs).
"""

import ipaddress
import json
import logging
import re
import socket
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import parse_qs, urljoin, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from bs4 import BeautifulSoup
from django.conf import settings

from attnd.colors import COLOR_RE, norm_color as _norm_color  # shared with the AI import's theme (API-023)
from studio.models import StudioMessage, StudioRun

logger = logging.getLogger(__name__)

USER_AGENT = 'Mozilla/5.0 (compatible; AttndStudio/1.0; +https://attnd.app)'
TIMEOUT = 15
MB = 1024 * 1024
MAX_HTML = 2 * MB
MAX_CSS = 500 * 1024
MAX_PAGES = 25
MAX_STYLESHEETS = 16
MAX_IMAGES, MAX_FONTS, MAX_PDFS = 120, 12, 8
FETCH_LIMIT = {'image': 10 * MB, 'svg': 1 * MB, 'font': 5 * MB, 'pdf': 20 * MB}  # = the asset library's limits
WORKERS = 4

IMAGE_EXT = ('png', 'jpg', 'jpeg', 'gif', 'webp', 'svg', 'avif')
FONT_EXT = ('woff2', 'woff', 'ttf', 'otf')
SKIP_EXT = ('css', 'js', 'json', 'xml', 'rss', 'zip', 'gz', 'mp4', 'mov', 'webm', 'mp3', 'wav', 'ics', 'doc', 'docx',
            'xls', 'xlsx', 'ppt', 'pptx', 'txt', 'csv', 'ico', 'eot', 'map')
SKIP_PATH = re.compile(r'/(?:wp-admin|wp-login|wp-json|cart|checkout|my-account|account|login|signin|sign-in|logout|'
                       r'feed|search|tag|author|cdn-cgi|xmlrpc)(?:/|\.|$)', re.I)
SPONSOR_RE = re.compile(r'sponsor|partner|presented|supporters?\b', re.I)
FLYER_RE = re.compile(r'flyer|poster', re.I)
GUIDE_RE = re.compile(r'guide|program|brochure|booklet', re.I)
MAP_RE = re.compile(r'\bmaps?\b|site-?plan|layout', re.I)


class SiteImportError(ValueError):
    pass


class LimitReached(SiteImportError):
    """The run's time or byte cap: stop fetching, keep what was copied."""


# ---------------------------------------------------------------------------
# Fetching with the SSRF guard
# ---------------------------------------------------------------------------

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
        check_url(newurl)  # every hop is checked, so a public URL can't redirect to an internal one
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_opener = build_opener(_SafeRedirects)


def fetch(url, limit):
    """(bytes, content_type, final_url). Raises SiteImportError for refused or failed fetches."""
    check_url(url)
    try:
        with _opener.open(Request(url, headers={'User-Agent': USER_AGENT, 'Accept': '*/*'}), timeout=TIMEOUT) as resp:
            length = resp.headers.get('Content-Length')
            if length and length.isdigit() and int(length) > limit:
                raise SiteImportError(f'{url} is too big.')
            data = resp.read(limit + 1)
            if len(data) > limit:
                raise SiteImportError(f'{url} is too big.')
            return data, (resp.headers.get('Content-Type') or '').split(';')[0].strip().lower(), resp.geturl()
    except SiteImportError:
        raise
    except Exception as exc:
        raise SiteImportError(f'Could not load {url}: {exc}') from exc


class Limits:
    """Per-run time and byte caps, shared by the download threads."""

    def __init__(self, seconds=None, max_bytes=None):
        self.deadline = time.monotonic() + (seconds if seconds is not None else settings.STUDIO_IMPORT_SECONDS)
        self.max_bytes = max_bytes if max_bytes is not None else settings.STUDIO_IMPORT_MAX_MB * MB
        self.used = 0
        self.hit = ''
        self._lock = threading.Lock()

    def check(self):
        if time.monotonic() > self.deadline:
            self.hit = self.hit or 'time'
            raise LimitReached('The import took too long, so Studio stopped copying files.')
        if self.used >= self.max_bytes:
            self.hit = self.hit or 'bytes'
            raise LimitReached('The website has more files than one import can copy, so Studio stopped copying.')

    def fetch(self, url, limit):
        self.check()
        data, ctype, final = fetch(url, limit)
        with self._lock:
            self.used += len(data)
        return data, ctype, final


# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------

def site_host(url):
    return (urlparse(url).hostname or '').lower().removeprefix('www.')


def _ext(url):
    last = urlparse(url).path.rsplit('/', 1)[-1]
    return last.rsplit('.', 1)[-1].lower() if '.' in last else ''


def _key(url):
    """Identity of a page URL: host without www, path without a trailing slash or index file, query; no fragment."""
    parts = urlparse(url)
    path = re.sub(r'/(?:index\.(?:html?|php))?$', '', parts.path) or '/'
    return f'{site_host(url)}{path}' + (f'?{parts.query}' if parts.query else '')


def _absolute(base, ref):
    ref = (ref or '').strip()
    if not ref or ref.startswith(('data:', 'javascript:', 'mailto:', 'tel:', '#', 'blob:', 'about:')):
        return ''
    url = urljoin(base, ref).split('#')[0]
    return url if url.startswith(('http://', 'https://')) else ''


def parse_srcset(value):
    """[(url, score)] from a srcset (HTML rules: the URL runs to the next whitespace, so URLs with commas, e.g.
    Cloudinary transformations, survive). Score = width for `w`, density × 1000 for `x`, 1000 when absent."""
    out, s, i, n = [], value or '', 0, len(value or '')
    while i < n:
        while i < n and (s[i].isspace() or s[i] == ','):
            i += 1
        j = i
        while j < n and not s[j].isspace():
            j += 1
        url = s[i:j]
        descriptor = ''
        if url.endswith(','):
            url = url.rstrip(',')
        else:
            k = s.find(',', j)
            k = n if k == -1 else k
            descriptor, j = s[j:k].strip(), k
        i = j
        if not url:
            continue
        score = 1000.0
        match = re.match(r'([\d.]+)([wx])', descriptor)
        if match:
            number = float(match.group(1))
            score = number if match.group(2) == 'w' else number * 1000
        out.append((url, score))
    return out


def best_from_srcset(value):
    candidates = parse_srcset(value)
    return max(candidates, key=lambda c: c[1])[0] if candidates else ''


def page_path(url, home_key, taken):
    """The new site's file for an old page: / → index.html, /info/parking/ → info-parking.html."""
    if _key(url) == home_key:
        return 'index.html'
    parts = urlparse(url)
    stem = re.sub(r'\.(?:html?|php|aspx?)$', '', parts.path.strip('/').lower())
    if parts.query:
        stem += '-' + parts.query
    stem = re.sub(r'[^a-z0-9]+', '-', stem).strip('-')[:60] or 'page'
    if stem == 'index':
        stem = 'home'
    name, n = f'{stem}.html', 2
    while name in taken:
        name, n = f'{stem}-{n}.html', n + 1
    taken.add(name)
    return name


# ---------------------------------------------------------------------------
# Capturing the site
# ---------------------------------------------------------------------------

FONT_FAMILY_RE = re.compile(r'font-family\s*:\s*([^;}{]+)', re.I)
FONT_FACE_RE = re.compile(r'@font-face\s*{([^}]*)}', re.I | re.S)
CSS_URL_RE = re.compile(r'url\(\s*([\'"]?)([^\'")]+?)\1\s*\)', re.I)
CSS_IMPORT_RE = re.compile(r'@import\s+(?:url\(\s*)?[\'"]?([^\'")\s;]+)', re.I)
KEEP_ATTRS = {'class', 'id', 'href', 'src', 'alt', 'title', 'aria-label', 'role', 'type', 'name', 'placeholder',
              'style', 'action', 'method', 'for', 'datetime'}


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


def _hint(tag, depth=4):
    """Class/id/alt/src words of a tag and its nearest ancestors (for logo and sponsor detection)."""
    words, node = [], tag
    for _ in range(depth):
        if node is None or not hasattr(node, 'get'):
            break
        words += [' '.join(node.get('class', []) or []), node.get('id', '') or '']
        node = node.parent
    return ' '.join([tag.get('alt', '') or '', tag.get('src', '') or '', *words]).lower()


def _image_src(tag):
    """The best URL of an <img>/<source>: the largest srcset candidate, else src or a lazy-load attribute."""
    for attr in ('srcset', 'data-srcset', 'data-lazy-srcset'):
        best = best_from_srcset(tag.get(attr, ''))
        if best:
            return best
    for attr in ('data-src', 'data-lazy-src', 'data-original', 'data-full', 'src'):
        value = tag.get(attr)
        if value and not value.startswith('data:'):
            return value
    return ''


class Capture:
    """Crawls the site and collects pages, CSS and the files to copy (nothing is stored here)."""

    def __init__(self, seeds, limits=None, progress=None):
        self.seeds = [s for s in dict.fromkeys(seeds) if s]
        self.hosts = {site_host(s) for s in self.seeds}
        self.limits = limits or Limits()
        self.progress = progress or (lambda text, **_: None)
        self.pages, self.css_parts, self.notes = [], [], []
        self.assets = {}  # url -> {url, kind, tags, alt, page, section, priority}
        self.colors, self.fonts = Counter(), Counter()
        self.google_fonts, self.font_faces = [], []
        self.logo_url = None
        self._stylesheets = set()

    # -- assets --------------------------------------------------------------
    def add(self, url, kind, tags=(), alt='', page='', section='', priority=5):
        if not url:
            return
        ext = _ext(url)
        if kind == 'image' and ext and ext not in IMAGE_EXT:
            return
        entry = self.assets.get(url)
        if entry is None:
            entry = self.assets[url] = {'url': url, 'kind': kind, 'tags': [], 'alt': '', 'page': page,
                                        'section': section, 'priority': priority}
        entry['tags'] = list(dict.fromkeys([*entry['tags'], *tags]))
        entry['alt'] = entry['alt'] or alt[:200]
        entry['section'] = entry['section'] or section[:140]
        entry['priority'] = min(entry['priority'], priority)

    # -- CSS -----------------------------------------------------------------
    def css(self, text, base, label, depth=0):
        """Record a stylesheet: url()s made absolute (against the stylesheet's own URL), fonts, backgrounds."""
        text = CSS_URL_RE.sub(lambda m: f'url("{_absolute(base, m.group(2)) or m.group(2)}")', text)
        for ref in CSS_IMPORT_RE.findall(text)[:6]:
            self.stylesheet(_absolute(base, ref), depth + 1)
        for block in FONT_FACE_RE.findall(text):
            family = re.search(r'font-family\s*:\s*[\'"]?([^;\'"]+)', block, re.I)
            urls = [u for _, u in CSS_URL_RE.findall(block) if _ext(u) in FONT_EXT]
            if not family or not urls:
                continue
            best = sorted(urls, key=lambda u: FONT_EXT.index(_ext(u)))[0]  # woff2 first
            weight = re.search(r'font-weight\s*:\s*([^;]+)', block, re.I)
            style = re.search(r'font-style\s*:\s*([^;]+)', block, re.I)
            self.font_faces.append({'family': family.group(1).strip(), 'url': best,
                                    'weight': weight.group(1).strip() if weight else 'normal',
                                    'style': style.group(1).strip() if style else 'normal'})
        faces = ''.join(FONT_FACE_RE.findall(text))
        for _, url in CSS_URL_RE.findall(text):
            if url.startswith('data:') or url in faces or _ext(url) in FONT_EXT:
                continue
            self.add(url, 'image', tags=['background'], page=label, priority=4)
        self.colors.update(_norm_color(c) for c in COLOR_RE.findall(text))
        self.fonts.update(f.strip().split(',')[0].strip(' \'"') for f in FONT_FAMILY_RE.findall(text))
        self.css_parts.append(f'/* {label} */\n{text}')

    def stylesheet(self, url, depth=0):
        if not url or url in self._stylesheets or depth > 1 or len(self._stylesheets) >= MAX_STYLESHEETS:
            return
        self._stylesheets.add(url)
        host = urlparse(url).hostname or ''
        if host == 'fonts.googleapis.com':  # sites may use Google Fonts directly (sites CSP), no copy needed
            for family in parse_qs(urlparse(url).query).get('family', []):
                self.google_fonts.extend(f for f in family.split('|') if f)
            return
        if host.endswith(('typekit.net', 'fonts.com', 'fontawesome.com')):
            self.notes.append(f'Skipped licensed font CSS from {host}.')
            return
        try:
            data, _, final = self.limits.fetch(url, MAX_CSS)
        except LimitReached:
            raise
        except SiteImportError as exc:
            logger.info('Studio import: stylesheet skipped: %s', exc)
            return
        self.css(data.decode('utf-8', 'replace'), final, urlparse(final).path or url, depth)

    # -- pages ---------------------------------------------------------------
    def crawl(self):
        if not self.seeds:
            raise SiteImportError('No website to bring in.')
        first = self.seeds[0]
        queue = list(self.seeds)
        root = f'{urlparse(first).scheme}://{urlparse(first).netloc}/'
        if _key(root) not in {_key(s) for s in self.seeds}:
            queue.append(root)  # the homepage carries the brand even when the event lives on an inner page
        seen = set()
        while queue and len(self.pages) < MAX_PAGES:
            url = queue.pop(0)
            key = _key(url)
            if key in seen:
                continue
            seen.add(key)
            try:
                self.limits.check()
                n = len(self.pages) + 1  # one row that counts up (the task card), not a row per page
                self.progress(f'Reading page {n}: {urlparse(url).path or "/"}', key='read',
                              done=f'Read {n} page{"" if n == 1 else "s"} of the website')
                data, ctype, final = self.limits.fetch(url, MAX_HTML)
            except LimitReached as exc:
                self.notes.append(str(exc))
                break
            except SiteImportError as exc:
                if not self.pages and url == first:
                    raise
                logger.info('Studio import: page skipped: %s', exc)
                continue
            seen.add(_key(final))
            if ctype == 'application/pdf':
                self.add(final, 'pdf', tags=['pdf'], priority=3)
                continue
            if 'html' not in ctype:
                if not self.pages and url == first:
                    raise SiteImportError(f'{url} is not a web page.')
                continue
            try:
                links = self.page(final, BeautifulSoup(data.decode('utf-8', 'replace'), 'html.parser'))
            except LimitReached as exc:
                self.notes.append(str(exc))
                break
            queue += [link for link in links if _key(link) not in seen]
        if not self.pages:
            raise SiteImportError(f'Could not read {self.seeds[0]}.')

    def page(self, url, soup):
        """Collect one page; returns the same-site links to crawl next (navigation first)."""
        path = urlparse(url).path or '/'
        is_home = not self.pages
        for link in soup.find_all('link', href=True):
            rel = ' '.join(link.get('rel') or []).lower()
            href = _absolute(url, link['href'])
            if 'stylesheet' in rel:
                self.stylesheet(href)
            elif 'icon' in rel:
                self.add(href, 'image', tags=['icon'], page=path, priority=1)
        for style in soup.find_all('style'):
            self.css(style.get_text(), url, f'{path} <style>')
        inline = []
        for tag in soup.find_all(style=True):
            inline.append(tag['style'])
            for _, ref in CSS_URL_RE.findall(tag['style']):
                heading = tag.find_previous(['h1', 'h2', 'h3'])
                self.add(_absolute(url, ref), 'image', tags=['background'], page=path,
                         section=heading.get_text(' ', strip=True) if heading else '', priority=3 if is_home else 4)
        self.colors.update(_norm_color(c) for c in COLOR_RE.findall(' '.join(inline)))
        for meta in soup.find_all('meta'):
            prop = (meta.get('property') or meta.get('name') or meta.get('itemprop') or '').lower()
            if prop in ('og:image', 'og:image:url', 'twitter:image', 'image') and meta.get('content'):
                self.add(_absolute(url, meta['content']), 'image', tags=['social'], page=path, priority=1)
        for script in soup.find_all('script', type='application/ld+json'):
            for logo in re.findall(r'"logo"\s*:\s*(?:\{[^}]*?"url"\s*:\s*)?"([^"]+)"', script.get_text()):
                absolute = _absolute(url, logo)
                self.add(absolute, 'image', tags=['logo'], page=path, priority=0)
                self.logo_url = self.logo_url or absolute
        for tag in soup.find_all(['img', 'source']):
            picture = tag.find_parent('picture')
            if tag.name == 'img' and picture is not None and any(
                    s.get('srcset') or s.get('data-srcset') for s in picture.find_all('source')):
                continue  # the <picture>'s fallback: its sources already give the largest version
            src = _absolute(url, _image_src(tag))
            if not src:
                continue
            hint = _hint(tag)
            tags = []
            in_header = tag.find_parent(['header', 'nav']) is not None
            if 'logo' in hint or (in_header and self.logo_url is None and tag.name == 'img'):
                tags.append('logo')
                self.logo_url = self.logo_url or src
            heading = tag.find_previous(['h1', 'h2', 'h3', 'h4'])
            section = heading.get_text(' ', strip=True) if heading else ''
            if SPONSOR_RE.search(hint) or SPONSOR_RE.search(section):
                tags.append('sponsor')
            if FLYER_RE.search(hint):
                tags.append('flyer')
            priority = 0 if 'logo' in tags else 2 if 'sponsor' in tags or is_home else 4
            alt = tag.get('alt', '')
            picture = tag.find_parent('picture') if tag.name == 'source' else None
            if picture is not None and picture.find('img') is not None:
                alt = picture.find('img').get('alt', '')
            self.add(src, 'image', tags=tags, alt=alt or '', page=path, section=section, priority=priority)
        for video in soup.find_all('video', poster=True):
            self.add(_absolute(url, video['poster']), 'image', tags=['poster'], page=path, priority=3)
        links = []
        anchors = soup.select('nav a[href], header a[href]') + soup.find_all('a', href=True)
        for a in anchors:
            href = _absolute(url, a.get('href'))
            if not href:
                continue
            ext, text = _ext(href), a.get_text(' ', strip=True)
            if ext == 'pdf':
                kind = 'map' if MAP_RE.search(text + href) else 'flyer' if FLYER_RE.search(text + href) else \
                    'guide' if GUIDE_RE.search(text + href) else 'document'
                self.add(href, 'pdf', tags=['pdf', kind], alt=text, page=path, priority=2)
            elif ext in IMAGE_EXT:
                self.add(href, 'image', tags=['gallery'], alt=text, page=path, priority=4)
            elif site_host(href) in self.hosts and ext not in SKIP_EXT and not SKIP_PATH.search(urlparse(href).path):
                links.append(href)
        self.pages.append({'url': url, 'outline': outline(soup), 'html': clean_html(soup, url)})
        return list(dict.fromkeys(links))

    def result(self):
        return {'url': self.pages[0]['url'], 'pages': self.pages, 'css': '\n'.join(self.css_parts),
                'colors': [c for c, _ in self.colors.most_common(14)],
                'fonts': [f for f, _ in self.fonts.most_common(8) if f and not f.startswith('var(')],
                'google_fonts': list(dict.fromkeys(self.google_fonts))[:8], 'font_faces': self.font_faces[:MAX_FONTS],
                'assets': list(self.assets.values()), 'logo_url': self.logo_url, 'notes': self.notes,
                'hosts': sorted(self.hosts)}


def clean_html(soup, base):
    """The page body without scripts, styles and noise; attributes trimmed; URLs absolute (so they can be swapped
    for the copied assets) and srcsets reduced to their best image."""
    body = BeautifulSoup(str(soup.body or soup), 'html.parser')
    for tag in body.find_all(['script', 'style', 'noscript', 'template', 'link', 'meta', 'iframe', 'object', 'embed']):
        if tag.name == 'iframe' and re.search(r'youtube|vimeo', tag.get('src', '')):
            tag.attrs = {'src': tag.get('src', '')}
            continue
        tag.decompose()
    for svg in body.find_all('svg'):
        if len(str(svg)) > 4000:
            svg.replace_with(body.new_tag('svg', attrs={'aria-label': svg.get('aria-label', 'icon')}))
    for tag in body.find_all(True):
        if tag.name in ('img', 'source'):
            src = _absolute(base, _image_src(tag))
            tag.attrs = {k: v for k, v in tag.attrs.items() if k in KEEP_ATTRS}
            if src:
                tag['src'] = src
        elif tag.name == 'svg' or tag.find_parent('svg') is not None:
            continue
        else:
            tag.attrs = {k: v for k, v in tag.attrs.items() if k in KEEP_ATTRS}
        for attr in ('href', 'action'):
            if tag.get(attr):
                tag[attr] = _absolute(base, tag[attr]) or tag[attr]
        if tag.get('style'):
            tag['style'] = CSS_URL_RE.sub(lambda m: f'url("{_absolute(base, m.group(2)) or m.group(2)}")',
                                          tag['style'])[:300]
    return re.sub(r'\s+', ' ', str(body)).strip()[:60_000]


def capture(url, seeds=(), limits=None, progress=None):
    """Everything about the site (see Capture.result). Kept as a function for callers and tests."""
    cap = Capture([url, *seeds], limits=limits, progress=progress)
    cap.crawl()
    return cap.result()


# ---------------------------------------------------------------------------
# Copying files into the asset library
# ---------------------------------------------------------------------------

def _plan(look):
    """The files to copy, most important first, within the per-kind caps."""
    entries = sorted(look['assets'], key=lambda a: (a['priority'], look['assets'].index(a)))
    caps = {'image': MAX_IMAGES, 'pdf': MAX_PDFS}
    out, counts = [], Counter()
    for entry in entries:
        if counts[entry['kind']] < caps.get(entry['kind'], 0):
            counts[entry['kind']] += 1
            out.append(entry)
    seen_fonts = set()
    for face in look['font_faces']:
        if face['url'] not in seen_fonts and len(seen_fonts) < MAX_FONTS:
            seen_fonts.add(face['url'])
            out.append({'url': face['url'], 'kind': 'font', 'tags': ['font'], 'alt': face['family'], 'page': '',
                        'section': '', 'priority': 1})
    return out


def _description(entry):
    where = f' page {entry["page"]}' if entry.get('page') else ''
    roles = [t for t in entry['tags'] if t not in ('imported',)]
    text = f'Imported from the old website{where}' + (f' ({", ".join(roles)})' if roles else '')
    if entry.get('alt'):
        text += f': "{entry["alt"]}"'
    if entry.get('section'):
        text += f', in the section "{entry["section"]}"'
    return (text + '.')[:1000]


def copy_assets(event, look, user, limits=None, progress=None):
    """Download the planned files (a few at a time) and store them as assets. Returns ({url: asset}, notes).
    Stops at the run's caps or when the library is full; whatever was copied stays."""
    from studio.assets import AssetError, create_asset
    limits = limits or Limits()
    progress = progress or (lambda text, **_: None)
    plan = _plan(look)
    copied, notes, failed = {}, [], 0

    def download(entry):
        limit = FETCH_LIMIT['svg'] if _ext(entry['url']) == 'svg' else FETCH_LIMIT[entry['kind']]
        data, _, _ = limits.fetch(entry['url'], limit)
        return data

    with ThreadPoolExecutor(max_workers=WORKERS, thread_name_prefix='studio-import') as pool:
        futures = {pool.submit(download, entry): entry for entry in plan}
        stop = False
        for done, future in enumerate(as_completed(futures), 1):
            entry = futures[future]
            if stop:
                future.cancel()
                continue
            try:
                data = future.result()
            except LimitReached as exc:
                if str(exc) not in notes:
                    notes.append(str(exc))
                continue
            except SiteImportError as exc:
                failed += 1
                logger.info('Studio import: file skipped: %s', exc)
                continue
            name = urlparse(entry['url']).path.rsplit('/', 1)[-1] or entry['kind']
            try:
                copied[entry['url']] = create_asset(
                    event, data, name, user=user, origin_url=entry['url'], describe=False, dedupe=True,
                    description=_description(entry), alt=entry.get('alt', ''), tags=['imported', *entry['tags']])
            except AssetError as exc:
                if 'library is full' in str(exc):
                    notes.append(str(exc))
                    stop = True
                else:
                    failed += 1
                    logger.info('Studio import: file refused: %s', exc)
            if done % 5 == 0 or done == len(plan):
                progress(f'Copied {len(copied)} of {len(plan)} images, fonts and PDFs…', key='copy',
                         done=f'Copied {len(copied)} image{"" if len(copied) == 1 else "s"}, fonts and PDFs')
    if failed:
        notes.append(f'{failed} file{"s" if failed != 1 else ""} could not be copied (missing, too big or not an '
                     f'image, font or PDF).')
    return copied, notes


def rewrite(text, url_map):
    """Swap the old site's file URLs for the copied assets' paths (longest first, so no URL eats another)."""
    for url in sorted(url_map, key=len, reverse=True):
        text = text.replace(url, url_map[url])
    return text


# ---------------------------------------------------------------------------
# Runs
# ---------------------------------------------------------------------------

def allowed_url(event, url):
    """Only websites the organizer gave as this event's sources (same host)."""
    host = site_host(url)
    return bool(host) and any(site_host(u) == host for u in (event.source_urls or []))


def start_import(site, url, user, note=''):
    """Queue the import run (it crawls the site in the background thread, then asks the AI)."""
    from studio import agent
    from studio.prompts import IMPORT_PROMPT
    if not allowed_url(site.event, url):
        raise SiteImportError('Only the event\'s own source websites can be brought in. Add it under the event\'s '
                              'website settings first.')
    seeds = [url] + [u for u in (site.event.source_urls or []) if site_host(u) == site_host(url) and u != url]
    prompt = IMPORT_PROMPT + (f'\n\nOrganizer notes: {note[:2000]}' if note else '')
    run = StudioRun.objects.create(site=site, created_by=user, kind=StudioRun.KIND_IMPORT, prompt=prompt,
                                   context={'import_url': url, 'seeds': seeds[:5]})
    StudioMessage.objects.create(site=site, role=StudioMessage.ROLE_USER, created_by=user, run=run,
                                 text=f'Bring {url} into Studio' + (f': {note}' if note else ''))
    agent.start(run)
    return run


def prepare_run(run, progress=None):
    """Before the AI runs (called by the agent for import runs): crawl the site and copy its files."""
    context = run.context or {}
    url = context.get('import_url')
    if not url or context.get('captured'):
        return
    limits = Limits()
    look = capture(url, seeds=context.get('seeds') or (), limits=limits, progress=progress)
    copied, notes = copy_assets(run.site.event, look, run.created_by, limits=limits, progress=progress)
    url_map = {u: a.site_path for u, a in copied.items()}
    pages, taken = [], set()
    home_key = _key(look['pages'][0]['url'])
    for page in look['pages']:
        page['path'] = page_path(page['url'], home_key, taken)
    page_map = {}
    for page in look['pages']:
        parts = urlparse(page['url'])
        for variant in {page['url'], page['url'].rstrip('/'), page['url'].rstrip('/') + '/',
                        f'{parts.scheme}://{parts.netloc}{parts.path}'}:
            page_map.setdefault(variant, page['path'])
    for page in look['pages']:
        html = rewrite(page['html'], url_map)
        html = re.sub(r'href="([^"]+)"', lambda m: f'href="{page_map.get(m.group(1), m.group(1))}"', html)
        pages.append({'url': page['url'], 'path': page['path'], 'outline': page['outline'], 'html': html})
    fonts = []
    for face in look['font_faces']:
        if face['url'] in copied:
            fonts.append({**{k: face[k] for k in ('family', 'weight', 'style')}, 'path': copied[face['url']].site_path})
    asset_hosts = sorted({site_host(u) for u in copied} | set(look['hosts']))
    pdfs = [a for u, a in copied.items() if a.kind == 'pdf']
    flyer = next((a for a in copied.values() if 'flyer' in a.tags), None)
    run.context = {
        'import_url': url, 'seeds': context.get('seeds') or [url], 'captured': True, 'pages': pages,
        'css': rewrite(look['css'], url_map)[:200_000], 'colors': look['colors'], 'fonts': look['fonts'],
        'google_fonts': look['google_fonts'], 'font_faces': fonts,
        'copied_assets': [{'from': u, 'path': a.site_path, 'id': str(a.id), 'kind': a.kind, 'tags': a.tags}
                          for u, a in copied.items()],
        'logo_asset_id': str(copied[look['logo_url']].id) if look.get('logo_url') in copied else None,
        'flyer_asset_id': str(flyer.id) if flyer else (str(pdfs[0].id) if pdfs else None),
        'notes': look['notes'] + notes, 'hosts': asset_hosts,
    }
    run.save(update_fields=['context'])


IMPORT_PAGE_BUDGET = 100_000  # characters of page HTML the model sees in total
HOME_PAGE_BUDGET = 30_000


def import_brief(context):
    """The IMPORTED SITE block of the model's context: pages (budgeted), the asset map, fonts, colours and CSS."""
    pages = context.get('pages') or []
    rest = max(len(pages) - 1, 1)
    per_page = max(3_000, (IMPORT_PAGE_BUDGET - HOME_PAGE_BUDGET) // rest)
    parts = [f'Old site: {context.get("import_url", "")} ({len(pages)} pages read). Rebuild every page below as the '
             f'"new file" named; links between them are already rewritten to those files.']
    if context.get('notes'):
        parts.append('Import notes: ' + ' '.join(context['notes'])[:1000])
    parts.append('COLOURS (most used first): ' + ', '.join(context.get('colors') or []))
    parts.append('FONT FAMILIES used: ' + ', '.join(context.get('fonts') or []))
    if context.get('google_fonts'):
        parts.append('GOOGLE FONTS (load them from fonts.googleapis.com): ' + ', '.join(context['google_fonts']))
    if context.get('font_faces'):
        parts.append('COPIED FONT FILES (write @font-face rules with these paths):\n'
                     + json.dumps(context['font_faces'], ensure_ascii=False))
    if context.get('logo_asset_id'):
        parts.append(f'LOGO asset id: {context["logo_asset_id"]}')
    if context.get('flyer_asset_id'):
        parts.append(f'FLYER / GUIDE asset id: {context["flyer_asset_id"]}')
    copied = context.get('copied_assets') or []
    parts.append(f'COPIED FILES ({len(copied)}; the old URLs in the pages below are already replaced by these paths):\n'
                 + json.dumps([{'path': a['path'], 'kind': a['kind'], 'tags': a.get('tags', [])} for a in copied],
                              ensure_ascii=False)[:20_000])
    for i, page in enumerate(pages):
        budget = HOME_PAGE_BUDGET if i == 0 else per_page
        parts.append(f'--- PAGE {page["url"]} → new file {page.get("path", "")} ---\n'
                     f'OUTLINE: {json.dumps(page.get("outline", {}), ensure_ascii=False)[:3000]}\n'
                     f'HTML: {page.get("html", "")[:budget]}')
    parts.append('CSS (old site, URLs already replaced):\n' + (context.get('css') or '')[:40_000])
    return '\n\n'.join(parts)
