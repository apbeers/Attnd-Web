"""Reading an event's website for the AI import (batch 7 A, owner Nov 22, 2026).

2VLQITGR had details on 2 of 43 schedule items: its listing pages show only names and times, each with an "Event
Details" link to a performer page the old crawl (15 pages per URL, breadth first) never reached, and three source
URLs of the same site crawled the same pages three times. This crawler:

- shares one budget across every source URL of a run (`MAX_PAGES` pages, `MAX_SECONDS` seconds) and never reads a
  page twice;
- reads the site's ``sitemap.xml`` (and ``Sitemap:`` lines in robots.txt, one level of sitemap index) for detail pages
  (artists, performers, speakers, vendors, sessions, menus…);
- visits pages in order of use: the start page, listing pages (schedule, lineup, map…), then detail pages (from the
  sitemap, or links like "Event details" / "More info", or URLs whose path names a detail kind), then the rest;
- keeps up to `MAX_PDFS` same-site PDFs whose link or address says schedule / lineup / map / menu / vendors / program
  (≤ 10 MB) and the largest 1–2 images (≥ 600 px) of schedule / lineup pages, for the AI to read as documents / images;
- reads the data JS sites embed (``__NEXT_DATA__``, ``__NUXT__``, ``window.__INITIAL_STATE__``) when a page's text is
  thin, and The Events Calendar's ``/wp-json/tribe/events/v1/events`` on WordPress sites;
- drops boilerplate lines repeated on 3+ pages (menus, footers) and orders the text by page priority, so the 200K
  characters sent to the model are the useful ones.

Every request goes through Studio's SSRF-guarded fetcher (public hosts, ports 80/443, guarded redirects)."""
import heapq
import io
import json
import logging
import re
import time
from collections import Counter
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

MAX_PAGES = 40
MAX_SECONDS = 120
PAGE_LIMIT = 5 * 1024 * 1024
PDF_LIMIT = 10 * 1024 * 1024
IMAGE_LIMIT = 8 * 1024 * 1024
MAX_PDFS = 3
MAX_SCHEDULE_IMAGES = 2
MIN_IMAGE_PX = 600
THIN_TEXT = 1500
EMBEDDED_LIMIT = 40_000

LISTING_WORDS = ('schedule', 'lineup', 'line-up', 'agenda', 'program', 'programme', 'timetable', 'map', 'venue',
                 'location', 'stage', 'vendor', 'exhibitor', 'food', 'speaker', 'artist', 'performer', 'event', 'info',
                 'faq', 'parking')
DETAIL_WORDS = ('artist', 'artists', 'performer', 'performers', 'speaker', 'speakers', 'vendor', 'vendors',
                'session', 'sessions', 'band', 'bands', 'exhibitor', 'exhibitors', 'menu', 'menus', 'lineup',
                'line-up', 'event', 'events', 'act', 'acts', 'program', 'talk', 'talks', 'workshop', 'workshops',
                'booth', 'booths', 'stage', 'stages', 'food-truck', 'food-trucks', 'maker', 'makers')
DETAIL_ANCHORS = re.compile(r'\b(event details?|more info|more information|details|learn more|read more|view( more)?|'
                            r'see more|bio|profile)\b', re.I)
PDF_WORDS = ('schedule', 'lineup', 'line-up', 'map', 'menu', 'vendor', 'program', 'programme', 'guide', 'agenda',
             'timetable')
SKIP_WORDS = ('login', 'signin', 'sign-in', 'cart', 'checkout', 'privacy', 'terms', 'cookie', 'account', 'wp-admin',
              'feed', 'mailto:', 'tel:', '.zip', '.mp3', '.mp4', '/tag/', '/author/', 'replytocom', '?share=')
EMBEDDED = (
    re.compile(r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>', re.S | re.I),
    re.compile(r'window\.__NUXT__\s*=\s*(.*?);?\s*</script>', re.S),
    re.compile(r'window\.__INITIAL_STATE__\s*=\s*(.*?);?\s*</script>', re.S),
)
PRI_START, PRI_LISTING, PRI_DETAIL, PRI_OTHER = 0, 1, 2, 3


def default_fetch(url, limit):
    from studio.importer import fetch
    return fetch(url, limit)


def _host(url):
    return (urlparse(url).hostname or '').lower().removeprefix('www.')


def _clean(url):
    return urlparse(url)._replace(fragment='').geturl()


def _path_words(url):
    return [w for w in re.split(r'[/_.?=&]+', urlparse(url).path.lower()) if w]


def is_detail_url(url):
    """A page about one performer / vendor / session: a detail word followed by a slug (/artists/jane-doe/)."""
    parts = [p for p in urlparse(url).path.lower().split('/') if p]
    return any(p in DETAIL_WORDS and i + 1 < len(parts) for i, p in enumerate(parts))


def page_priority(url):
    lower = url.lower()
    if any(w in lower for w in SKIP_WORDS):
        return None
    if is_detail_url(url):
        return PRI_DETAIL
    return PRI_LISTING if any(w in lower for w in LISTING_WORDS) else PRI_OTHER


def page_text(html):
    """Visible text with one line per block (so boilerplate lines can be dropped), JSON-LD kept, and the data a JS
    site embeds when its visible text is thin."""
    soup = BeautifulSoup(html, 'html.parser')
    jsonld = [t.get_text() for t in soup.find_all('script', type='application/ld+json')]
    for tag in soup(['script', 'style', 'noscript', 'meta', 'head', 'svg', 'template']):
        tag.decompose()
    lines = [' '.join(line.split()) for line in soup.get_text(separator='\n').splitlines()]
    text = '\n'.join(line for line in lines if line)
    if len(text) < THIN_TEXT:
        embedded = embedded_data(html)
        if embedded:
            text += '\n\n[Data embedded in the page]\n' + embedded
    if jsonld:
        text += '\n\n[JSON-LD structured data]\n' + '\n'.join(jsonld)
    return text


def embedded_data(html):
    for pattern in EMBEDDED:
        match = pattern.search(html)
        if match:
            raw = match.group(1).strip()
            try:
                raw = json.dumps(json.loads(raw), ensure_ascii=False, separators=(',', ':'))
            except ValueError:
                pass
            return raw[:EMBEDDED_LIMIT]
    return ''


def _links(html, base_url):
    """(url, anchor text) of same-site links, and (src, alt, width, height) of images."""
    soup = BeautifulSoup(html, 'html.parser')
    host = _host(base_url)
    links = []
    for a in soup.find_all('a', href=True):
        url = _clean(urljoin(base_url, a['href']))
        if urlparse(url).scheme in ('http', 'https') and _host(url) == host:
            links.append((url, ' '.join(a.get_text(' ').split())[:120]))
    images = []
    for img in soup.find_all('img'):
        src = img.get('src') or img.get('data-src') or ''
        if src and not src.startswith('data:'):
            images.append(_clean(urljoin(base_url, src)))
    return links, images


def _sitemap_urls(host_root, fetch, deadline):
    """Same-site detail pages listed in sitemap.xml / robots.txt's sitemaps (one level of index)."""
    found, sitemaps = [], [urljoin(host_root, '/sitemap.xml')]
    try:
        robots, _, _ = fetch(urljoin(host_root, '/robots.txt'), 256 * 1024)
        sitemaps += re.findall(r'(?im)^\s*sitemap:\s*(\S+)', robots.decode('utf-8', 'replace'))
    except Exception:
        pass
    seen, queue = set(), list(dict.fromkeys(sitemaps))[:3]
    while queue and len(seen) < 6 and time.monotonic() < deadline:
        url = queue.pop(0)
        if url in seen or _host(url) != _host(host_root):
            continue
        seen.add(url)
        try:
            body, _, _ = fetch(url, 4 * 1024 * 1024)
        except Exception:
            continue
        locs = re.findall(r'<loc>\s*([^<\s]+)\s*</loc>', body.decode('utf-8', 'replace'))
        for loc in locs:
            if loc.endswith('.xml') or 'sitemap' in loc.rsplit('/', 1)[-1]:
                if len(queue) < 4:
                    queue.append(loc)
            elif _host(loc) == _host(host_root) and is_detail_url(loc):
                found.append(_clean(loc))
    return list(dict.fromkeys(found))


def _dedupe_boilerplate(pages):
    """Lines (short ones) seen on 3+ pages stay only on the first page that has them."""
    counts = Counter()
    for _, _, text in pages:
        counts.update({line for line in text.split('\n') if len(line) < 200})
    common = {line for line, n in counts.items() if n >= 3}
    seen, out = set(), []
    for pri, url, text in pages:
        keep = []
        for line in text.split('\n'):
            if line in common:
                if line in seen:
                    continue
                seen.add(line)
            keep.append(line)
        out.append((pri, url, '\n'.join(keep)))
    return out


def _image_size(data):
    try:
        from PIL import Image
        with Image.open(io.BytesIO(data)) as im:
            return im.size
    except Exception:
        return (0, 0)


class Crawl:
    """One run over every source URL. `pages` = [(priority, url, text)], `attachments` = PDFs and schedule images
    for the model, `colors` (optional Counter) = the pages' colours (API-023)."""

    def __init__(self, fetch=None, max_pages=MAX_PAGES, max_seconds=MAX_SECONDS, on_page=None, colors=None):
        self.fetch = fetch or default_fetch
        self.max_pages, self.on_page, self.colors = max_pages, on_page, colors
        self.deadline = time.monotonic() + max_seconds
        self.visited, self.pages, self.attachments = set(), [], []
        self.pdfs, self.images, self.wordpress = [], [], set()
        self._queue, self._order = [], 0

    def _push(self, url, depth, priority=None):
        url = _clean(url)
        if url in self.visited:
            return
        pri = page_priority(url) if priority is None else priority
        if pri is None:
            return
        self._order += 1
        heapq.heappush(self._queue, (pri, self._order, url, depth))

    def run(self, urls, crawl=True, max_depth=2):
        for url in urls:
            self._push(url, 0, PRI_START)
        if crawl:
            for root in dict.fromkeys(f'{urlparse(u).scheme}://{urlparse(u).netloc}/' for u in urls):
                for detail in _sitemap_urls(root, self.fetch, self.deadline)[:self.max_pages]:
                    self._push(detail, max_depth, PRI_DETAIL)
        budget = self.max_pages if crawl else len(urls)
        while self._queue and len(self.visited) < budget and time.monotonic() < self.deadline:
            pri, _, url, depth = heapq.heappop(self._queue)
            if url in self.visited:
                continue
            self.visited.add(url)
            try:
                body, ctype, final = self.fetch(url, PAGE_LIMIT)
            except Exception as exc:
                logger.info('Crawl: %s (%s)', url, exc)
                continue
            if 'html' not in ctype:
                continue
            html = body.decode('utf-8', 'replace')
            if self.colors is not None:
                from attnd.colors import html_colors
                html_colors(html, self.colors)
            text = page_text(html)
            if text:
                self.pages.append((pri, url, text))
                if self.on_page:
                    self.on_page(url)
            if 'wp-content' in html or 'tribe-events' in html:
                self.wordpress.add(f'{urlparse(final).scheme}://{urlparse(final).netloc}/')
            if not crawl:
                continue
            links, images = _links(html, final)
            for link, anchor in links:
                lower = link.lower()
                if lower.split('?')[0].endswith('.pdf'):
                    if any(w in lower or w in anchor.lower() for w in PDF_WORDS):
                        self.pdfs.append(link)
                    continue
                if depth < max_depth:
                    detail = bool(DETAIL_ANCHORS.search(anchor)) or is_detail_url(link)
                    self._push(link, depth + 1, PRI_DETAIL if detail and page_priority(link) is not None else None)
            if pri in (PRI_START, PRI_LISTING) and any(w in url.lower() for w in ('schedule', 'lineup', 'line-up',
                                                                                     'program', 'timetable', 'agenda')):
                self.images += images[:8]
        if crawl:
            self._extras()
        return self

    def _extras(self):
        for url in list(dict.fromkeys(self.pdfs))[:MAX_PDFS]:
            if time.monotonic() > self.deadline:
                break
            try:
                data, ctype, _ = self.fetch(url, PDF_LIMIT)
            except Exception:
                continue
            if data[:5] == b'%PDF-':
                self.attachments.append({'name': url.rsplit('/', 1)[-1][:80] or 'schedule.pdf',
                                         'mime': 'application/pdf', 'data': data})
        sized = []
        for url in list(dict.fromkeys(self.images))[:8]:
            if time.monotonic() > self.deadline:
                break
            try:
                data, ctype, _ = self.fetch(url, IMAGE_LIMIT)
            except Exception:
                continue
            if ctype not in ('image/png', 'image/jpeg', 'image/webp', 'image/gif'):
                continue
            w, h = _image_size(data)
            if max(w, h) >= MIN_IMAGE_PX:
                sized.append((w * h, url, ctype, data))
        for _, url, ctype, data in sorted(sized, key=lambda s: -s[0])[:MAX_SCHEDULE_IMAGES]:
            self.attachments.append({'name': url.rsplit('/', 1)[-1][:80], 'mime': ctype, 'data': data})
        for root in self.wordpress:
            if time.monotonic() > self.deadline:
                break
            try:
                data, ctype, _ = self.fetch(urljoin(root, '/wp-json/tribe/events/v1/events?per_page=50'), 2 * 1024 * 1024)
                events = json.loads(data).get('events') or []
            except Exception:
                continue
            if events:
                keep = [{k: e.get(k) for k in ('title', 'start_date', 'end_date', 'description', 'url', 'venue',
                                               'cost', 'categories') if e.get(k)} for e in events]
                self.pages.append((PRI_LISTING, urljoin(root, '/wp-json/tribe/events/v1/events'),
                                   '[The Events Calendar data]\n' + json.dumps(keep, ensure_ascii=False)[:EMBEDDED_LIMIT]))

    def text(self):
        """The pages as one text, boilerplate dropped, most useful pages first."""
        pages = sorted(_dedupe_boilerplate(self.pages), key=lambda p: p[0])
        return '\n\n---\n\n'.join(f'## Source: {url}\n\n{text}' for _, url, text in pages if text.strip())
