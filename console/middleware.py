from urllib.parse import urlencode

from django.conf import settings
from django.shortcuts import redirect
from django.urls import reverse

from console import twofactor


class TwoFactorMiddleware:
    """Signed in with the password but not past the second step yet (console/twofactor.py)? Then the
    console, the Django admin and the password-change pages send you to the code prompt, or, for staff
    without an authenticator, to enrollment. Everything else (public site, API, sign-out, the two-factor
    pages themselves) is unaffected."""

    def __init__(self, get_response):
        self.get_response = get_response
        self.protected = ('/console/', '/' + settings.ADMIN_URL_PATH, '/accounts/password_change/')

    def __call__(self, request):
        if request.path.startswith(self.protected) and request.user.is_authenticated:
            step = twofactor.pending_step(request)
            if step is not None:
                target = reverse('two_factor_verify' if step == 'verify' else 'two_factor_setup')
                if request.method == 'GET':
                    target += '?' + urlencode({'next': request.get_full_path()})
                return redirect(target)
        return self.get_response(request)


class ComponentFragmentMiddleware:
    """The Ask Attnd chat submits a component's form to the real view with the header ``X-Attnd-Component``. When
    the view answers with its page again (the form had errors), the ``{% component %}`` tag stored that
    component's chat rendering on the request: answer with just that (JSON), so the chat redraws the form with its
    errors in place. Redirects (saved) and everything else pass through untouched."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        fragment = getattr(request, '_component_fragment', None)
        if (fragment and request.method == 'POST' and response.status_code == 200
                and 'text/html' in response.get('Content-Type', '')
                and request.headers.get('X-Attnd-Component') == fragment['component']):
            from django.http import JsonResponse
            return JsonResponse({'html': fragment['html'], 'component': fragment['component'],
                                 'ids': fragment['ids'], 'invalid': True})
        return response
