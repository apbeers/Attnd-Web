"""Passkey (WebAuthn) endpoints, called with fetch() by console/static/console/passkeys.js.

All are POST with a JSON body and the CSRF token in X-CSRFToken; they answer JSON:
{"redirect": url} on success, {"error": text} (shown to the person) otherwise.

- auth/options + auth/verify: a passkey sign-in from the sign-in page (usernameless, also autofill), which
  logs the account in and completes the second step at once; or, with mode "second_step", the second step
  after a password sign-in (that account's passkeys only). Failed verifications count per IP.
- register/options + register/verify: add a passkey to the signed-in account. Allowed once the account is
  past the second step, or while it still has to set one up (staff enrollment), never while a second step
  is pending (the password alone must not be enough to add a passkey).
"""

import json
import logging

from django.contrib import messages
from django.contrib.auth import login
from django.http import JsonResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.views.decorators.http import require_POST

from attnd.ratelimit import RateLimit, client_ip
from console import passkeys, twofactor
from console.views_auth import NEW_RECOVERY_CODES, PASSKEY_PROMPT, _next_url, _with_next
from console.webauthn import WebAuthnError

logger = logging.getLogger(__name__)

PASSKEY_IP = RateLimit('passkey_ip')
LIMITED = 'Too many passkey attempts from your network. Please wait 15 minutes and try again.'
FAILED = 'That passkey didn’t work here. Try again, or sign in with your email and password.'
BACKEND = 'console.auth_backends.EmailOrUsernameBackend'


def _body(request) -> dict:
    try:
        data = json.loads(request.body or b'{}')
    except (ValueError, UnicodeDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _error(text: str, status: int = 400) -> JsonResponse:
    return JsonResponse({'error': text}, status=status)


def _next_from(request, data: dict) -> str:
    """The validated `next` URL from the JSON body (same rules as the forms)."""
    return _next_url(request, str(data.get('next') or ''))


# ---------------------------------------------------------------------------
# Sign-in / second step
# ---------------------------------------------------------------------------

def _second_step_user(request):
    """The account whose second step is pending and who has passkeys, or None."""
    user = request.user
    if user.is_authenticated and twofactor.pending_step(request) == 'verify' and twofactor.has_passkeys(user):
        return user
    return None


@require_POST
def auth_options(request):
    if PASSKEY_IP.exceeded(client_ip(request)):
        return _error(LIMITED, 429)
    data = _body(request)
    if data.get('mode') == 'second_step':
        user = _second_step_user(request)
        if user is None:
            return _error('There is no sign-in waiting for a passkey.', 403)
        return JsonResponse(passkeys.authentication_options(request, user))
    return JsonResponse(passkeys.authentication_options(request))


@require_POST
def auth_verify(request):
    ip = client_ip(request)
    if PASSKEY_IP.exceeded(ip):
        return _error(LIMITED, 429)
    data = _body(request)
    next_url = _next_from(request, data)
    second_step = data.get('mode') == 'second_step'
    user = _second_step_user(request) if second_step else None
    if second_step and user is None:
        return _error('There is no sign-in waiting for a passkey.', 403)
    try:
        passkey = passkeys.finish_authentication(request, data.get('credential'), user)
    except WebAuthnError as exc:
        PASSKEY_IP.hit(ip)
        logger.info('Passkey sign-in rejected: %s', exc)
        return _error(FAILED)
    if not second_step:
        login(request, passkey.user, backend=BACKEND)
    twofactor.mark_verified(request)  # user verification on the authenticator = both factors
    request.session.pop(PASSKEY_PROMPT, None)
    return JsonResponse({'redirect': next_url})


# ---------------------------------------------------------------------------
# Adding a passkey
# ---------------------------------------------------------------------------

def _may_register(request) -> bool:
    return request.user.is_authenticated and twofactor.pending_step(request) in (None, 'setup')


@require_POST
def register_options(request):
    if not _may_register(request):
        return _error('Sign in fully before adding a passkey.', 403)
    return JsonResponse(passkeys.registration_options(request))


@require_POST
def register_verify(request):
    if not _may_register(request):
        return _error('Sign in fully before adding a passkey.', 403)
    data = _body(request)
    next_url = _next_from(request, data)
    try:
        passkey = passkeys.finish_registration(request, data.get('credential'), data.get('name', ''))
    except WebAuthnError as exc:
        logger.info('Passkey registration rejected: %s', exc)
        return _error('That passkey couldn’t be added. Please try again.')
    twofactor.mark_verified(request)  # the account now has a second factor; this session has used it
    request.session.pop(PASSKEY_PROMPT, None)
    codes = twofactor.first_recovery_codes(request.user)
    messages.success(request, f'Passkey “{passkey.name}” added. Next time, sign in with it: no password needed.')
    if codes:
        request.session[NEW_RECOVERY_CODES] = codes
        return JsonResponse({'redirect': _with_next('two_factor_recovery_codes', next_url)})
    return JsonResponse({'redirect': next_url})


@require_POST
def prompt_dismiss(request):
    """"Not now" on the "Sign in faster with a passkey" prompt: never shown to this account again."""
    if request.user.is_authenticated:
        row = twofactor.security(request.user)
        row.passkey_prompt_dismissed = True
        row.save(update_fields=['passkey_prompt_dismissed'])
    request.session.pop(PASSKEY_PROMPT, None)
    return redirect(_next_url(request) if request.POST.get('next') else reverse('console:dashboard'))


def take_prompt(request) -> bool:
    """True once after a password sign-in, for the "Sign in faster with a passkey" banner in the console
    (base.html; JavaScript then shows it only where a built-in passkey authenticator is available)."""
    user = getattr(request, 'user', None)
    if user is None or not user.is_authenticated or not request.session.get(PASSKEY_PROMPT):
        return False
    if twofactor.pending_step(request) is not None:
        return False  # not yet: after the second step
    request.session.pop(PASSKEY_PROMPT, None)
    return not twofactor.has_passkeys(user) and not twofactor.security(user).passkey_prompt_dismissed
