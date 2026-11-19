from django.http import HttpResponse, HttpResponseNotFound

from studio import hosting

# Paths other middleware would answer on any host (WhiteNoise: static files and the public/ root files).
_BLOCKED_PREFIXES = ('/static/', '/media/', '/favicon.ico', '/apple-touch-icon', '/.well-known/')


class SitesHostMiddleware:
    """On SITES_HOST (Studio's hosted sites, a separate registrable domain) only the site routes exist.

    - Requests are routed with studio.urls_sites (the console, admin, API and accounts don't exist there: 404).
    - /static/, /media/ and WhiteNoise's root files are refused before WhiteNoise can serve them.
    - Service worker script fetches (`Service-Worker: script`) are refused: no service workers on hosted sites.
    - Responses never set cookies, carry the sites' own CSP (frame-ancestors = the console only) and no
      X-Frame-Options (the console frames previews).
    Must be the first middleware, so it sees every response last.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not hosting.is_sites_request(request):
            return self.get_response(request)
        request.urlconf = 'studio.urls_sites'
        request.is_sites_host = True
        if request.path.startswith(_BLOCKED_PREFIXES) or request.headers.get('Service-Worker'):
            response = HttpResponseNotFound('Not found', content_type='text/plain')
        elif request.path == '/robots.txt':
            response = HttpResponse('User-agent: *\nAllow: /\nDisallow: /*/_preview/\n', content_type='text/plain')
        else:
            response = self.get_response(request)
        return self.harden(response)

    @staticmethod
    def harden(response):
        response.cookies.clear()
        for header in ('Set-Cookie', 'X-Frame-Options', 'Content-Security-Policy-Report-Only',
                       'Service-Worker-Allowed'):
            if header in response:
                del response[header]
        if not getattr(response, 'studio_csp', None):
            response['Content-Security-Policy'] = hosting.sites_csp()
        else:
            response['Content-Security-Policy'] = response.studio_csp
        response['Cross-Origin-Resource-Policy'] = 'cross-origin'
        response['Referrer-Policy'] = 'strict-origin-when-cross-origin'
        response['X-Content-Type-Options'] = 'nosniff'
        if response.has_header('Vary'):
            parts = [p.strip() for p in response['Vary'].split(',') if p.strip().lower() != 'cookie']
            if parts:
                response['Vary'] = ', '.join(parts)
            else:
                del response['Vary']
        return response
