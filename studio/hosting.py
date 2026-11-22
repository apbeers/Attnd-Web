"""Hosting rules for Studio sites: the sites host, URLs, preview tokens, the sites' CSP and page rendering."""

import html
import re
import time

from django.conf import settings
from django.core import signing

PREVIEW_SALT = 'studio.preview'
EDITOR_PREVIEW_SECONDS = 2 * 3600


def _strip_port(host):
    host = (host or '').strip().lower()
    for default in (':443', ':80'):
        if host.endswith(default):
            return host[:-len(default)]
    return host


def sites_host():
    return _strip_port(getattr(settings, 'SITES_HOST', ''))


def is_old_sites_request(request):
    """The sites' old host (settings.OLD_SITES_HOST, the herokuapp.com name) after the move to a real domain."""
    old = getattr(settings, 'OLD_SITES_HOST', '')
    if not old:
        return False
    try:
        requested = request.get_host()
    except Exception:  # DisallowedHost
        requested = request.META.get('HTTP_HOST', '')
    return _strip_port(requested) == old


def is_sites_request(request):
    host = sites_host()
    if not host:
        return False
    try:
        requested = request.get_host()
    except Exception:  # DisallowedHost
        requested = request.META.get('HTTP_HOST', '')
    return _strip_port(requested) == host


def sites_origin():
    host = getattr(settings, 'SITES_HOST', '')
    if not host:
        return ''
    local = host.startswith(('127.0.0.1', 'localhost')) and settings.DEBUG
    return f"{'http' if local else 'https'}://{host}"


def site_url(site, path=''):
    origin = sites_origin()
    return f'{origin}/{site.slug}/{path}' if origin else ''


# ---------------------------------------------------------------------------
# Preview tokens: /<slug>/_preview/<token>/…
# ---------------------------------------------------------------------------

def make_preview_token(site, seconds=EDITOR_PREVIEW_SECONDS, version=None, run=None):
    payload = {'s': site.pk, 'k': site.preview_secret[:8], 'e': int(time.time()) + int(seconds)}
    if version is not None:
        payload['v'] = version.pk
    if run is not None:
        payload['r'] = run.pk
    return signing.dumps(payload, salt=PREVIEW_SALT, compress=True)


def read_preview_token(site, token):
    """The payload if the token is genuine, for this site, not expired and not revoked; else None."""
    try:
        payload = signing.loads(token, salt=PREVIEW_SALT)
    except signing.BadSignature:
        return None
    if not isinstance(payload, dict) or payload.get('s') != site.pk:
        return None
    if payload.get('k') != site.preview_secret[:8] or int(payload.get('e', 0)) < time.time():
        return None
    return payload


def preview_url(site, **kwargs):
    origin = sites_origin()
    if not origin:
        return ''
    return f'{origin}/{site.slug}/_preview/{make_preview_token(site, **kwargs)}/'


# ---------------------------------------------------------------------------
# The sites' Content Security Policy
# ---------------------------------------------------------------------------

def sites_csp():
    ancestors = ' '.join(getattr(settings, 'STUDIO_CONSOLE_ORIGINS', []) or ["'none'"])
    return '; '.join([
        "default-src 'self'",
        "script-src 'self' 'unsafe-inline'",
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
        "img-src 'self' data: blob: https:",
        "font-src 'self' data: https://fonts.gstatic.com",
        "connect-src 'self'",
        "media-src 'self' https:",
        "frame-src https://www.youtube-nocookie.com https://player.vimeo.com https://www.openstreetmap.org",
        "worker-src 'none'",
        "manifest-src 'self'",
        f'frame-ancestors {ancestors}',
        "form-action 'self'",
        "base-uri 'self'",
        "object-src 'none'",
    ])


SVG_CSP = "default-src 'none'; style-src 'unsafe-inline'; img-src data:; sandbox"


# ---------------------------------------------------------------------------
# Rendering a hosted page
# ---------------------------------------------------------------------------

_HEAD_RE = re.compile(r'<head(\s[^>]*)?>', re.I)
_BODY_END_RE = re.compile(r'</body\s*>', re.I)
_BASE_RE = re.compile(r'<base\b[^>]*>', re.I)

FOOTER_STYLE = ('display:block;margin:0;padding:10px 16px;background:#111;color:#bbb;'
                'font:13px/1.4 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;text-align:center')
LINK_STYLE = 'color:#fff;text-decoration:underline'


HASH_LINK_SCRIPT = (
    "<script>document.addEventListener('click',function(e){var a=e.target.closest&&e.target.closest('a[href^=\"#\"]');"
    "if(!a||e.defaultPrevented)return;var id=decodeURIComponent(a.getAttribute('href').slice(1));"
    "var t=id?document.getElementById(id):null;if(id&&!t)return;e.preventDefault();"
    "(t||document.body).scrollIntoView({behavior:'smooth'});"
    "history.replaceState(null,'',location.pathname+location.search+(id?'#'+id:''));});</script>")


_CANONICAL_RE = re.compile(r'<link[^>]+rel=["\']?canonical', re.I)
_FONT_PRECONNECT_RE = re.compile(r'<link[^>]+preconnect[^>]+fonts\.gstatic\.com|<link[^>]+fonts\.gstatic\.com[^>]+preconnect',
                                 re.I)
FONT_PRECONNECT = ('<link rel="preconnect" href="https://fonts.googleapis.com">'
                   '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>')


_A_RE = re.compile(r'<a\b[^>]*>', re.I)
_FORM_RE = re.compile(r'<form\b[^>]*>', re.I)
_INPUT_RE = re.compile(r'<input\b[^>]*>', re.I)
_SCRIPT_SRC_RE = re.compile(r'<script\b[^>]*\bsrc\s*=\s*["\']?((?:https?:)?//[^"\'\s>]+)[^>]*>\s*</script\s*>', re.I)
_ATTR = r'\b{}\s*=\s*("[^"]*"|\'[^\']*\'|[^\s>]+)'
_BODY_RE = re.compile(r'<body\b[^>]*>', re.I)


def _attr(tag, name):
    m = re.search(_ATTR.format(name), tag, re.I)
    return html.unescape(m.group(1).strip('"\'')) if m else None


def _set_attr(tag, name, value):
    value = html.escape(value, quote=True)
    if re.search(_ATTR.format(name), tag, re.I):
        return re.sub(_ATTR.format(name), f'{name}="{value}"', tag, count=1, flags=re.I)
    return re.sub(r'\s*/?>$', lambda m: f' {name}="{value}"' + m.group(0), tag, count=1)


def _external(url):
    from urllib.parse import urlsplit
    if not url or not url.lower().startswith(('http://', 'https://', '//')):
        return False
    host = (urlsplit(url if not url.startswith('//') else 'https:' + url).hostname or '').lower()
    return host != sites_host().split(':')[0]


def sanitize(text):
    """What every served page goes through (batch 7 §6, also enforced at save by the quality check's errors):
    outbound links get rel="noopener nofollow ugc"; a form that posts to another site is disabled; password and
    card fields are removed; scripts from other sites are removed (the CSP blocks them anyway)."""
    from studio.quality import CARD_WORDS

    def link(m):
        tag = m.group(0)
        return _set_attr(tag, 'rel', 'noopener nofollow ugc') if _external(_attr(tag, 'href')) else tag

    def form(m):
        tag = m.group(0)
        if _external(_attr(tag, 'action')):
            tag = _set_attr(_set_attr(tag, 'action', '#'), 'data-attnd-blocked', 'other-site')
        return tag

    def field(m):
        tag = m.group(0)
        words = ' '.join(_attr(tag, k) or '' for k in ('name', 'id', 'autocomplete', 'placeholder'))
        if (_attr(tag, 'type') or '').lower() == 'password' or 'password' in words.lower() \
                or CARD_WORDS.search(words) or (_attr(tag, 'autocomplete') or '').lower().startswith('cc-'):
            return '<!-- removed: sites can’t ask for passwords or card details -->'
        return tag

    text = _A_RE.sub(link, text)
    text = _FORM_RE.sub(form, text)
    text = _INPUT_RE.sub(field, text)
    return _SCRIPT_SRC_RE.sub(lambda m: m.group(0) if not _external(m.group(1)) else '', text)


def render_page(source, base_href, preview=False, bridge=False, canonical='', head='', body_start='', body_end=''):
    """Insert <base href> (relative links then work at /<slug>/ and later on a custom domain at /), the Attnd footer
    with "Report this site", the preview ribbon and the editor's selection bridge; `head` / `body_start` /
    `body_end` = SEO tags, the Get the app banner and the analytics beacon (studio.seo)."""
    text = sanitize(_BASE_RE.sub('', source))
    base = f'<base href="{html.escape(base_href)}">'
    if preview:
        base += '<meta name="robots" content="noindex">'
    elif canonical and not _CANONICAL_RE.search(text):
        base += f'<link rel="canonical" href="{html.escape(canonical)}">'
    base += head
    if 'fonts.googleapis.com' in text and not _FONT_PRECONNECT_RE.search(text):
        # Lighthouse (Nov 22, 2026): Google Fonts' stylesheet blocks the first paint; opening both connections
        # straight away saves a round trip or two on phones.
        base += FONT_PRECONNECT
    match = _HEAD_RE.search(text)
    if match:
        text = text[:match.end()] + base + text[match.end():]
    else:
        text = f'<head>{base}</head>' + text
    if body_start:
        m = _BODY_RE.search(text)
        text = text[:m.end()] + body_start + text[m.end():] if m else body_start + text
    footer = (f'<footer data-attnd-host-footer style=\'{FOOTER_STYLE}\'>Made with '
              f'<a style="{LINK_STYLE}" href="{html.escape(settings.SITE_URL)}/" rel="noopener">Attnd</a> · '
              f'<a style="{LINK_STYLE}" href="_attnd/report" rel="nofollow">Report this site</a></footer>')
    if preview:
        footer += ('<div data-attnd-preview-ribbon style="position:fixed;z-index:2147483646;bottom:12px;left:12px;'
                   'padding:4px 10px;border-radius:999px;background:#431f86;color:#fff;font:600 12px/1.4 sans-serif;'
                   'pointer-events:none">Preview</div>')
    # In-page links ("#faq") would resolve against <base> and leave the page: keep them on the page.
    footer += HASH_LINK_SCRIPT + body_end
    if bridge:
        footer += '<script src="_attnd/bridge.js" defer></script>'
    matches = list(_BODY_END_RE.finditer(text))
    if matches:
        end = matches[-1]
        text = text[:end.start()] + footer + text[end.start():]
    else:
        text += footer
    return text
