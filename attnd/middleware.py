"""Response compression for the app's own responses (WhiteNoise already serves static files pre-compressed).

The apps' JSON (the v2 bundle is ~20 KB for the DEMO event, ~4 KB gzipped) and the HTML pages went out
uncompressed: Heroku's router doesn't compress. URLSession and Android's HttpURLConnection ask for gzip and
decompress it transparently.
"""

import re

from django.middleware.gzip import GZipMiddleware

_COMPRESSIBLE = re.compile(r'^(text/|application/(json|javascript|xml|manifest\+json)|image/svg\+xml)', re.I)


class CompressionMiddleware(GZipMiddleware):
    """Django's GZipMiddleware (with its BREACH mitigation), limited to text responses that are already in
    memory, and keeping the response's ETag as it is.

    Django turns a strong ETag weak (`W/"…"`) when it compresses. Our ETags name a content version (the
    bundle's `"e<id>-v<version>"`, the Studio data version), and the views compare
    `If-None-Match` to them exactly, so a weakened tag would turn every conditional poll into a full
    download. The tag still identifies the same content in either encoding.
    """

    def process_response(self, request, response):
        if response.streaming or not _COMPRESSIBLE.match(response.get('Content-Type', '')):
            return response
        etag = response.get('ETag')
        response = super().process_response(request, response)
        if etag is not None:
            response.headers['ETag'] = etag
        return response


class SecurityHeadersMiddleware:
    """Permissions-Policy on our own pages (stage G; settings.PERMISSIONS_POLICY). Studio's hosted sites set their
    own headers (studio.middleware answers them before this runs)."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        from django.conf import settings
        response = self.get_response(request)
        policy = getattr(settings, 'PERMISSIONS_POLICY', '')
        sites = (getattr(settings, 'SITES_HOST', '') or '').split(':')[0]
        if sites and request.get_host().split(':')[0] == sites:
            return response  # Studio's hosted sites: their own headers
        if policy and 'Permissions-Policy' not in response:
            response['Permissions-Policy'] = policy
        return response


def indexable():
    """Only production (SITE_URL https://attnd.app) may be indexed; dev.attnd.app and its sites host never."""
    from django.conf import settings
    return (getattr(settings, 'SITE_URL', '') or '').rstrip('/') == 'https://attnd.app'


class NoIndexMiddleware:
    """Off production: robots.txt disallows everything and every response says `X-Robots-Tag: noindex, nofollow`
    (also the Studio sites on dev.attnd.site). First in MIDDLEWARE, so it wraps the sites host too."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if indexable():
            return self.get_response(request)
        if request.path == '/robots.txt':
            from django.http import HttpResponse
            response = HttpResponse('User-agent: *\nDisallow: /\n', content_type='text/plain')
        else:
            response = self.get_response(request)
        response['X-Robots-Tag'] = 'noindex, nofollow'
        return response


class WwwRedirectMiddleware:
    """www.attnd.app → https://attnd.app/<same path and query> (301), one canonical host for search engines. The
    well-known files (Apple's AASA must never redirect, assetlinks) are served on www as they are."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        host = request.get_host().split(':')[0].lower()
        if host.startswith('www.') and not request.path.startswith('/.well-known/'):
            from urllib.parse import urlsplit

            from django.conf import settings
            from django.http import HttpResponsePermanentRedirect
            site = urlsplit(getattr(settings, 'SITE_URL', '') or 'https://attnd.app')
            if host == f'www.{site.hostname}':
                query = request.META.get('QUERY_STRING', '')
                return HttpResponsePermanentRedirect(f'{site.scheme}://{site.netloc}{request.path}'
                                                     + (f'?{query}' if query else ''))
        return self.get_response(request)
