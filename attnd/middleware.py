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
