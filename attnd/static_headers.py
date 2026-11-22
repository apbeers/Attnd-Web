"""Cache headers for the files WhiteNoise serves at the site root (public/), stage H."""
import os

ICONS = ('.ico', '.png', '.svg')


def root_headers(headers, path, url):
    if '/static/' in url or url.startswith('/static/'):
        return
    if url.endswith(ICONS):
        headers['Cache-Control'] = 'public, max-age=86400, stale-while-revalidate=604800'
    elif os.path.basename(url) in ('robots.txt', 'site.webmanifest'):
        headers['Cache-Control'] = 'public, max-age=3600'
