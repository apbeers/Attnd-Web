"""Colours used on web pages, counted (shared by Studio's importer and the AI import's theme, API-023)."""

import re
from collections import Counter

COLOR_RE = re.compile(r'#(?:[0-9a-fA-F]{6}|[0-9a-fA-F]{3})\b|rgba?\([^)]{5,40}\)')
_STYLE_BLOCK_RE = re.compile(r'<style[^>]*>(.*?)</style>', re.I | re.S)
_STYLE_ATTR_RE = re.compile(r'\sstyle\s*=\s*("([^"]*)"|\'([^\']*)\')', re.I)
_THEME_META_RE = re.compile(r'<meta[^>]+name=["\'](?:theme-color|msapplication-TileColor)["\'][^>]*>', re.I)
_CONTENT_RE = re.compile(r'content\s*=\s*["\']([^"\']+)["\']', re.I)


def norm_color(c):
    """Lower-case #rrggbb for 3-digit hex; other values (rgb()) as they are, lower case."""
    c = c.lower()
    if re.fullmatch(r'#[0-9a-f]{3}', c):
        c = '#' + ''.join(ch * 2 for ch in c[1:])
    return c


def as_hex(c):
    """#rrggbb for a hex or an opaque rgb()/rgba() value, else ''."""
    c = norm_color(c.strip())
    if re.fullmatch(r'#[0-9a-f]{6}', c):
        return c
    m = re.fullmatch(r'rgba?\(\s*(\d{1,3})[\s,]+(\d{1,3})[\s,]+(\d{1,3})(?:[\s,/]+([\d.]+%?))?\s*\)', c)
    if not m:
        return ''
    alpha = m.group(4)
    if alpha is not None:
        a = float(alpha[:-1]) / 100 if alpha.endswith('%') else float(alpha)
        if a < 0.9:
            return ''
    r, g, b = (min(255, int(v)) for v in m.groups()[:3])
    return f'#{r:02x}{g:02x}{b:02x}'


def count(text, counter=None):
    """Add every colour in a stylesheet / style text to a Counter (normalised, as Studio's importer counts)."""
    counter = counter if counter is not None else Counter()
    counter.update(norm_color(c) for c in COLOR_RE.findall(text or ''))
    return counter


def html_colors(html, counter=None):
    """The colours of one HTML page without fetching its stylesheets: <style> blocks, style attributes and the
    theme-color meta (counted three times: the site's own brand colour)."""
    counter = counter if counter is not None else Counter()
    html = html or ''
    for block in _STYLE_BLOCK_RE.findall(html):
        count(block, counter)
    count(' '.join(m[1] or m[2] for m in _STYLE_ATTR_RE.findall(html)), counter)
    for meta in _THEME_META_RE.findall(html):
        m = _CONTENT_RE.search(meta)
        if m and as_hex(m.group(1)):
            counter[as_hex(m.group(1))] += 3
    return counter


def most_used_hex(counter, limit=12):
    """The most used colours as #rrggbb (rgb() values converted, translucent ones skipped)."""
    merged = Counter()
    for c, n in counter.items():
        h = as_hex(c)
        if h:
            merged[h] += n
    return [c for c, _ in merged.most_common(limit)]
