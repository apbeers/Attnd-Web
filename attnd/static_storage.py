"""Static files storage (Lighthouse, Nov 22, 2026): WhiteNoise's hashed + compressed storage, plus a small, safe CSS
minifier for our own stylesheets at collectstatic time (no new dependency). JavaScript is not minified: a stdlib-only
JS minifier can't be made safe (regex literals, ASI, template strings), and gzip / brotli already shrink it.

The minifier only removes comments and collapses whitespace: it never touches strings, never removes the space
around `:` (a descendant `a :hover` differs from `a:hover`) or `+` / `-` (calc()), and keeps `/*! … */` notices."""

import re

from django.core.files.base import ContentFile
from whitenoise.storage import CompressedManifestStaticFilesStorage

OWN_PREFIXES = ('design/', 'console/', 'agent/', 'home/', 'diagnostics/', 'studio/', 'platform_admin/', 'prospects/', 'credit/')
_TOKEN = re.compile(r'("(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\')|(/\*!.*?\*/)|(/\*.*?\*/)|(\s+)', re.S)
_AROUND = re.compile(r'\s*([{};,>])\s*')


def minify_css(text):
    out = []
    pos = 0
    for m in _TOKEN.finditer(text):
        out.append(_squeeze(text[pos:m.start()]))
        string, notice, comment, space = m.groups()
        if string:
            out.append(string)
        elif notice:
            out.append(notice)
        elif comment:
            out.append(' ')
        else:
            out.append(' ')
        pos = m.end()
    out.append(_squeeze(text[pos:]))
    joined = ''.join(out)
    # Spaces around { } ; , > outside strings: split on strings again so they are never touched.
    parts = re.split(r'("(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\')', joined)
    for i in range(0, len(parts), 2):
        parts[i] = _AROUND.sub(r'\1', re.sub(r' {2,}', ' ', parts[i]))
        parts[i] = parts[i].replace(';}', '}')
    return ''.join(parts).strip() + '\n'


def _squeeze(chunk):
    return re.sub(r'\s+', ' ', chunk)


class MinifyingStaticFilesStorage(CompressedManifestStaticFilesStorage):
    def _save(self, name, content):
        if name.endswith('.css') and not name.endswith('.min.css') and name.startswith(OWN_PREFIXES):
            try:
                if hasattr(content, 'seek'):
                    content.seek(0)  # post_process hands over files it has already read (to hash them)
                text = content.read().decode('utf-8')
            except UnicodeDecodeError:
                content.seek(0)
                return super()._save(name, content)
            content = ContentFile(minify_css(text).encode('utf-8'))
        return super()._save(name, content)
