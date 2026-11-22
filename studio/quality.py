"""Studio's pre-publish check (batch 7 §6): static analysis of a site version, no AI and no network.

`check(site, version)` returns the issues shown above Publish, each {code, level, page, text, fix}: level `error`
blocks publishing (forms to other sites, password / card fields, outside scripts), `warning` and `info` don't.
`fix` names an automatic fix `autofix()` can apply (trivial ones only: alt text, viewport, lang, title, meta
description); everything else is the designer's job ("Update the site" sends the list to design_website).

Checks: broken links and images, alt text, contrast of simple CSS rules, heading order, page weight, mobile layout
(viewport, wide fixed widths, tiny text), SEO basics (title, description, lang, one h1), safety (forms, password /
card fields, outside scripts, shortener links) and drift (dates / times / the event name the pages hard-code that
no longer match the event, `drift()`), plus translations whose source page changed since (`translations`).
"""

import hashlib
import re
from datetime import timedelta
from html.parser import HTMLParser
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

ERROR, WARNING, INFO = 'error', 'warning', 'info'
PAGE_WEIGHT_WARN = 1_500_000      # bytes of code + referenced images per page
PAGE_WEIGHT_INFO = 700_000
WIDE_PX = 600                     # a fixed width wider than a phone
SHORTENERS = {'bit.ly', 'tinyurl.com', 't.co', 'goo.gl', 'ow.ly', 'is.gd', 'buff.ly', 'rebrand.ly', 'cutt.ly',
              'shorturl.at', 'tiny.cc', 'rb.gy', 'shorte.st', 'adf.ly', 'bl.ink', 'lnkd.in'}
BAD_TLDS = ('.zip', '.mov', '.xyz', '.top', '.click', '.country', '.gq', '.tk', '.ml', '.cf')
CARD_WORDS = re.compile(r'card[-_ ]?(number|num|no)|cc[-_]?(num|number)|\bcvv\b|\bcvc\b|security[-_ ]?code|iban',
                        re.I)
SOURCE_META = 'attnd-source'      # <meta name="attnd-source" content="index.html sha1"> on translated pages
LANGS = {'es': 'Spanish', 'fr': 'French', 'de': 'German', 'it': 'Italian', 'pt': 'Portuguese', 'zh': 'Chinese',
         'ja': 'Japanese', 'ko': 'Korean', 'vi': 'Vietnamese', 'ar': 'Arabic', 'nl': 'Dutch', 'pl': 'Polish'}


class _Page(HTMLParser):
    """Everything the checks need from one HTML page."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links, self.images, self.scripts, self.forms, self.inputs = [], [], [], [], []
        self.headings, self.metas, self.styles, self.inline_styles = [], {}, [], []
        self.html_lang = None
        self.has_main = False
        self.title = ''
        self.text = []
        self._in = []

    def handle_starttag(self, tag, attrs):
        a = {k: (v or '') for k, v in attrs}
        if tag == 'main' or a.get('role') == 'main':
            self.has_main = True
        if tag == 'html':
            self.html_lang = a.get('lang', '')
        elif tag == 'a' and 'href' in a:
            self.links.append(a)
        elif tag == 'img':
            self.images.append(a)
        elif tag == 'script':
            self.scripts.append(a)
        elif tag == 'form':
            self.forms.append(a)
        elif tag in ('input', 'select', 'textarea'):
            self.inputs.append({'tag': tag, **a})
        elif tag == 'meta':
            key = (a.get('name') or a.get('property') or '').lower()
            if key:
                self.metas[key] = a.get('content', '')
        elif tag == 'link' and a.get('rel', '').lower() == 'stylesheet' and a.get('href'):
            self.links.append({'href': a['href'], '_css': '1'})
        elif re.fullmatch(r'h[1-6]', tag):
            self.headings.append(int(tag[1]))
        if a.get('style'):
            self.inline_styles.append(a['style'])
        if tag not in ('img', 'input', 'meta', 'link', 'br', 'hr', 'source', 'area', 'base', 'col', 'wbr'):
            self._in.append(tag)

    def handle_endtag(self, tag):
        if tag in self._in:
            while self._in and self._in.pop() != tag:
                pass

    def handle_data(self, data):
        current = self._in[-1] if self._in else ''
        if current == 'title':
            self.title += data
        elif current == 'style':
            self.styles.append(data)
        elif current not in ('script', 'noscript'):
            if data.strip():
                self.text.append(data.strip())


def parse(source):
    page = _Page()
    try:
        page.feed(source or '')
        page.close()
    except Exception:  # never let a broken page stop the check
        pass
    return page


def _issue(code, level, page, text, fix=''):
    return {'code': code, 'level': level, 'page': page, 'text': text, 'fix': fix}


def _local(href):
    """True for links that stay on the site (relative paths)."""
    if not href or href.startswith(('#', 'mailto:', 'tel:', 'sms:', 'javascript:', 'data:')):
        return False
    return not urlsplit(href).scheme and not href.startswith('//')


def _resolve(page_path, href):
    """The site file a relative link points at (pages are served with <base href=/<slug>/>)."""
    path = urlsplit(href).path.lstrip('/')
    if path.startswith('./'):
        path = path[2:]
    return path


def _exists(files, path, assets):
    if path.startswith('_attnd/'):
        if path.startswith('_attnd/assets/'):
            asset_id = path[len('_attnd/assets/'):].split('/', 1)[0]
            return asset_id in assets
        return path in ('_attnd/attnd.js', '_attnd/data.json', '_attnd/qr.svg', '_attnd/report') \
            or path.startswith('_attnd/forms/')
    if path in ('', 'index'):
        return 'index.html' in files
    if path.endswith('/'):
        return path + 'index.html' in files
    return any(c in files for c in (path, path + '.html', path + '/index.html'))


# ---------------------------------------------------------------------------
# Contrast (WCAG 2.2 maths) for simple rules: color + background in one rule or inline style
# ---------------------------------------------------------------------------

HEX = re.compile(r'#([0-9a-f]{3}|[0-9a-f]{6})\b', re.I)
NAMED = {'white': 'ffffff', 'black': '000000', 'gray': '808080', 'grey': '808080', 'silver': 'c0c0c0',
         'yellow': 'ffff00', 'red': 'ff0000', 'blue': '0000ff', 'lightgray': 'd3d3d3', 'lightgrey': 'd3d3d3'}


def _rgb(value):
    value = (value or '').strip().lower()
    if value in NAMED:
        value = '#' + NAMED[value]
    m = HEX.fullmatch(value)
    if not m:
        return None
    h = m.group(1)
    if len(h) == 3:
        h = ''.join(c * 2 for c in h)
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _lum(rgb):
    def ch(c):
        c /= 255
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (ch(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a, b):
    la, lb = sorted((_lum(a), _lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def _decls(block):
    out = {}
    for part in block.split(';'):
        if ':' in part:
            k, v = part.split(':', 1)
            out[k.strip().lower()] = v.replace('!important', '').strip()
    return out


def _rules(css):
    css = re.sub(r'/\*.*?\*/', '', css or '', flags=re.S)
    for m in re.finditer(r'([^{}]+)\{([^{}]*)\}', css):
        yield m.group(1).strip(), _decls(m.group(2))


def _low_contrast(blocks):
    """[(selector, ratio, fg, bg)] for rules setting both a text colour and a solid background under 4.5:1."""
    found = []
    for selector, d in blocks:
        fg = _rgb(d.get('color'))
        bg = _rgb(d.get('background-color') or d.get('background', '').split(' ')[0])
        if fg and bg:
            ratio = contrast(fg, bg)
            if ratio < 4.5:
                found.append((selector, ratio, d.get('color'), d.get('background-color') or d.get('background')))
    return found


# ---------------------------------------------------------------------------
# Drift: what the pages hard-code vs. the event now
# ---------------------------------------------------------------------------

MONTHS = {m: i for i, m in enumerate(['jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov',
                                      'dec'], 1)}
DATE_RE = re.compile(r'\b(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+(\d{1,2})(?:st|nd|rd|th)?\b'
                     r'(?:\s*[–-]\s*(\d{1,2})(?:st|nd|rd|th)?\b)?', re.I)
TIME_RE = re.compile(r'\b(\d{1,2})(?::(\d{2}))?\s*([ap])\.?m\.?\b', re.I)


def _event_days(event):
    if not event.start_time:
        return set()
    tz = ZoneInfo(event.timezone or 'UTC')
    start = event.start_time.astimezone(tz).date()
    end = (event.end_time or event.start_time).astimezone(tz).date()
    days, day = set(), start
    while day <= end and len(days) < 60:
        days.add((day.month, day.day))
        day += timedelta(days=1)
    return days


def _clock(dt, tz):
    local = dt.astimezone(tz)
    return local.hour, local.minute


def drift(site, version, items=None):
    """Issues for text that no longer matches the event: dates outside its days, an item's time that changed, and
    the event's name missing from the home page after a rename."""
    event = site.event
    tz = ZoneInfo(event.timezone or 'UTC')
    days = _event_days(event)
    if items is None:
        items = list(event.scheduleditem_set.filter(hidden=False).only('name', 'start_time'))
    out = []
    for path in version.pages:
        page = parse(version.files[path])
        text = ' '.join(page.text)
        if days:
            wrong = []
            for m in DATE_RE.finditer(text):
                month = MONTHS.get(m.group(1).lower()[:3])
                day = int(m.group(2))
                if month and 1 <= day <= 31 and (month, day) not in days:
                    wrong.append(m.group(0))
            if wrong:
                out.append(_issue('drift_date', WARNING, path, f'Says {", ".join(dict.fromkeys(wrong[:3]))}, but the '
                                  f'event is on other days now.', 'update_site'))
        lower = text.lower()
        for item in items:
            name = (item.name or '').strip()
            if len(name) < 4 or not item.start_time:
                continue
            at = lower.find(name.lower())
            if at < 0:
                continue
            window = text[max(0, at - 60): at + len(name) + 60]
            times = [(int(h) % 12 + (12 if ap.lower() == 'p' else 0), int(mm or 0)) for h, mm, ap in TIME_RE.findall(window)]
            if times and _clock(item.start_time, tz) not in times:
                hour, minute = _clock(item.start_time, tz)
                shown = f'{(hour % 12) or 12}:{minute:02d} {"PM" if hour >= 12 else "AM"}'
                out.append(_issue('drift_time', WARNING, path, f'“{name}” is shown at another time; it starts at '
                                  f'{shown} now.', 'update_site'))
        if path == 'index.html' and event.name and event.name.lower() not in (lower + ' ' + page.title.lower()):
            out.append(_issue('drift_name', WARNING, path, f'The home page doesn’t say “{event.name}”: was the event '
                              f'renamed?', 'update_site'))
    return out


# ---------------------------------------------------------------------------
# Translations: <lang>/… pages that carry their source page's hash
# ---------------------------------------------------------------------------

def source_hash(text):
    return hashlib.sha1((text or '').encode()).hexdigest()[:12]


def translations(version):
    """{lang: {'pages': [...], 'stale': [...]}} for the version's translated folders (es/, fr/ …)."""
    out = {}
    for path in version.pages:
        lang, _, rest = path.partition('/')
        if lang not in LANGS or not rest:
            continue
        entry = out.setdefault(lang, {'name': LANGS[lang], 'pages': [], 'stale': []})
        entry['pages'].append(rest)
        meta = parse(version.files[path]).metas.get(SOURCE_META, '')
        source_path = rest
        current = version.files.get(source_path)
        if current is not None and meta.split(' ')[-1] != source_hash(current):
            entry['stale'].append(rest)
    return out


# ---------------------------------------------------------------------------
# The check
# ---------------------------------------------------------------------------

def check(site, version, include_drift=True):
    from studio.models import StudioAsset
    files = version.files or {}
    assets = {str(a['id']): a for a in StudioAsset.objects.filter(event=site.event).values('id', 'size', 'alt')}
    issues = []
    if 'index.html' not in files:
        return {'issues': [_issue('no_home', ERROR, '', 'The site needs a home page (index.html).')],
                'counts': {ERROR: 1, WARNING: 0, INFO: 0}, 'fixable': 0, 'blocked': True}
    css_blocks = [rule for path, text in files.items() if path.endswith('.css') for rule in _rules(text)]
    for path in version.pages:
        page = parse(files[path])
        blocks = list(css_blocks) + [rule for s in page.styles for rule in _rules(s)] + \
            [('(inline style)', _decls(s)) for s in page.inline_styles]
        # Links and images
        broken = sorted({link['href'] for link in page.links if _local(link['href'])
                         and not _exists(files, _resolve(path, link['href']), assets)})
        if broken:
            issues.append(_issue('broken_link', WARNING, path, f'Broken link{"s" if len(broken) > 1 else ""}: '
                                 f'{", ".join(broken[:4])}{" …" if len(broken) > 4 else ""}.'))
        missing = sorted({img.get('src', '') for img in page.images if _local(img.get('src', ''))
                          and not _exists(files, _resolve(path, img['src']), assets)})
        if missing:
            issues.append(_issue('broken_image', WARNING, path, f'Missing image{"s" if len(missing) > 1 else ""}: '
                                 f'{", ".join(missing[:3])}.'))
        no_alt = [img for img in page.images if 'alt' not in img]
        if no_alt:
            issues.append(_issue('alt', WARNING, path, f'{len(no_alt)} image{"s" if len(no_alt) > 1 else ""} without '
                                 f'alt text.', 'alt'))
        # Headings
        h = page.headings
        if path.endswith('.html') and h.count(1) == 0:
            issues.append(_issue('h1', INFO, path, 'No main heading (h1).'))
        elif h.count(1) > 1:
            issues.append(_issue('h1', INFO, path, f'{h.count(1)} main headings (h1); one is best.'))
        if not page.has_main:
            issues.append(_issue('main', WARNING, path, 'No main landmark (<main>): screen readers can’t jump to the '
                                 'content.', 'main'))
        skipped = next((f'h{a} → h{b}' for a, b in zip(h, h[1:]) if b > a + 1), '')
        if skipped:
            issues.append(_issue('headings', INFO, path, f'Headings skip a level ({skipped}).'))
        # Contrast
        low = _low_contrast(blocks)
        if low:
            sel, ratio, fg, bg = low[0]
            issues.append(_issue('contrast', WARNING, path, f'Low contrast: {fg} on {bg} ({ratio:.1f}:1, needs 4.5:1)'
                                 f' in {sel[:60]}{f" and {len(low) - 1} more" if len(low) > 1 else ""}.'))
        # Mobile
        if 'viewport' not in page.metas:
            issues.append(_issue('viewport', WARNING, path, 'No mobile viewport: phones show it zoomed out.',
                                 'viewport'))
        wide = [int(w) for _, d in blocks for k, v in d.items() if k in ('width', 'min-width')
                for w in re.findall(r'^(\d+)px$', v) if int(w) > WIDE_PX]
        if wide:
            issues.append(_issue('wide', WARNING, path, f'A fixed width of {max(wide)}px is wider than a phone '
                                 f'(use max-width or %).'))
        tiny = [float(s) for _, d in blocks for s in re.findall(r'^(\d+(?:\.\d+)?)px$', d.get('font-size', ''))
                if float(s) < 12]
        if tiny:
            issues.append(_issue('tiny_text', INFO, path, f'Text as small as {min(tiny):g}px is hard to read on '
                                 f'phones.'))
        # Weight
        weight = len(files[path].encode()) + sum(len(t.encode()) for p, t in files.items()
                                                  if p.endswith(('.css', '.js')))
        for img in page.images:
            src = _resolve(path, img.get('src', ''))
            if src.startswith('_attnd/assets/'):
                weight += (assets.get(src[len('_attnd/assets/'):].split('/', 1)[0]) or {}).get('size') or 0
        if weight > PAGE_WEIGHT_WARN:
            issues.append(_issue('weight', WARNING, path, f'Heavy page: {weight / 1e6:.1f} MB. Use fewer or smaller '
                                 f'images.'))
        elif weight > PAGE_WEIGHT_INFO:
            issues.append(_issue('weight', INFO, path, f'Page weight {weight / 1e6:.1f} MB.'))
        # SEO basics
        if not page.title.strip():
            issues.append(_issue('title', WARNING, path, 'No page title.', 'title'))
        if 'description' not in page.metas:
            issues.append(_issue('description', INFO, path, 'No meta description for search results.',
                                 'description'))
        if not page.html_lang:
            issues.append(_issue('lang', INFO, path, 'The page doesn’t say its language.', 'lang'))
        # Safety (also enforced when the page is served)
        for form in page.forms:
            action = form.get('action', '')
            if action and not _local(action) and not action.startswith('#'):
                issues.append(_issue('form_origin', ERROR, path, f'A form sends to another site ({action[:60]}). '
                                     f'Forms may only use Attnd’s form handler (data-attnd-form).'))
        for field in page.inputs:
            words = ' '.join(field.get(k, '') for k in ('name', 'id', 'autocomplete', 'placeholder'))
            if field.get('type', '').lower() == 'password' or 'password' in words.lower():
                issues.append(_issue('password_field', ERROR, path, 'Sites can’t ask for passwords.'))
                break
            if CARD_WORDS.search(words) or field.get('autocomplete', '').startswith('cc-'):
                issues.append(_issue('card_field', ERROR, path, 'Sites can’t ask for card or bank details.'))
                break
        outside = sorted({s.get('src') for s in page.scripts if s.get('src') and not _local(s['src'])})
        if outside:
            issues.append(_issue('outside_script', ERROR, path, f'Scripts from other sites aren’t allowed: '
                                 f'{", ".join(outside[:3])}.'))
        risky = sorted({urlsplit(link['href']).hostname or '' for link in page.links
                        if (urlsplit(link['href']).hostname or '').lower().removeprefix('www.') in SHORTENERS
                        or (urlsplit(link['href']).hostname or '').lower().endswith(BAD_TLDS)})
        if risky:
            issues.append(_issue('risky_link', WARNING, path, f'Links through {", ".join(risky[:3])}: visitors can’t '
                                 f'see where they lead. Link to the real address.'))
    for lang, entry in translations(version).items():
        if entry['stale']:
            issues.append(_issue('translation_stale', WARNING, f'{lang}/', f'The {entry["name"]} version is out of '
                                 f'date ({", ".join(entry["stale"][:3])} changed since).', 'update_translation'))
    if include_drift:
        issues.extend(drift(site, version))
    order = {ERROR: 0, WARNING: 1, INFO: 2}
    issues.sort(key=lambda i: (order[i['level']], i['page'], i['code']))
    counts = {lvl: sum(1 for i in issues if i['level'] == lvl) for lvl in (ERROR, WARNING, INFO)}
    return {'issues': issues, 'counts': counts, 'blocked': counts[ERROR] > 0,
            'fixable': sum(1 for i in issues if i['fix'] in AUTO_FIXES)}


# ---------------------------------------------------------------------------
# Automatic fixes (trivial ones only)
# ---------------------------------------------------------------------------

AUTO_FIXES = ('alt', 'viewport', 'lang', 'title', 'description', 'main')
IMG_NO_ALT = re.compile(r'<img\b(?![^>]*\balt\s*=)([^>]*?)(/?)>', re.I)


def _alt_for(src, assets_alt):
    asset_id = src.split('_attnd/assets/', 1)[-1].split('/', 1)[0] if '_attnd/assets/' in src else ''
    if assets_alt.get(asset_id):
        return assets_alt[asset_id]
    stem = src.rsplit('/', 1)[-1].rsplit('.', 1)[0]
    return re.sub(r'[-_]+', ' ', stem).strip().capitalize()[:120]


def _insert_head(text, tag):
    m = re.search(r'<head(\s[^>]*)?>', text, re.I)
    if m:
        return text[:m.end()] + tag + text[m.end():]
    return tag + text


def autofix(site, files):
    """(new files, number of fixes) with the trivial issues fixed. Never changes anything else."""
    import html as h
    from studio.models import StudioAsset
    event = site.event
    assets_alt = {str(a): alt for a, alt in StudioAsset.objects.filter(event=event).values_list('id', 'alt')}
    out, fixes = dict(files), 0
    description = re.sub(r'\s+', ' ', (event.description or '')).strip()[:155]
    for path in [p for p in files if p.endswith('.html')]:
        text = out[path]
        page = parse(text)

        def add_alt(m):
            src = re.search(r'\bsrc\s*=\s*["\']([^"\']*)', m.group(1), re.I)
            return f'<img{m.group(1)} alt="{h.escape(_alt_for(src.group(1) if src else "", assets_alt))}"{m.group(2)}>'
        new, n = IMG_NO_ALT.subn(add_alt, text)
        fixes += n
        text = new
        if 'viewport' not in page.metas:
            text = _insert_head(text, '<meta name="viewport" content="width=device-width, initial-scale=1">')
            fixes += 1
        if not page.html_lang:
            lang = path.split('/', 1)[0] if path.split('/', 1)[0] in LANGS else 'en'
            new, n = re.subn(r'<html\b(?![^>]*\blang=)', f'<html lang="{lang}"', text, count=1, flags=re.I)
            if n:
                text, fixes = new, fixes + 1
        if not page.title.strip():
            name = event.name if path == 'index.html' else f'{path.rsplit(".", 1)[0].split("/")[-1].replace("-", " ").title()} · {event.name}'
            text = re.sub(r'<title>\s*</title>', '', text, flags=re.I)
            text = _insert_head(text, f'<title>{h.escape(name)}</title>')
            fixes += 1
        if 'description' not in page.metas and description:
            text = _insert_head(text, f'<meta name="description" content="{h.escape(description)}">')
            fixes += 1
        if not page.has_main:
            wrapped = _wrap_main(text)
            if wrapped != text:
                text, fixes = wrapped, fixes + 1
        out[path] = text
    return out, fixes


def _wrap_main(text):
    """<main> around the page's content: after the first </header> (else <body>), before the last <footer> (else
    </body>). Unchanged when the page has no body."""
    body = re.search(r'<body\b[^>]*>', text, re.I)
    end = list(re.finditer(r'</body\s*>', text, re.I))
    if not body or not end:
        return text
    start = body.end()
    header = re.search(r'</header\s*>', text[start:], re.I)
    if header:
        start += header.end()
    stop = end[-1].start()
    footers = [m for m in re.finditer(r'<footer\b', text[start:stop], re.I)]
    if footers:
        stop = start + footers[-1].start()
    return text[:start] + '<main id="main">' + text[start:stop] + '</main>' + text[stop:]
