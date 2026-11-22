"""Recent sign-in check ("sudo mode", owner Nov 21, 2026): risky actions need a sign-in within the last 20 minutes.

The risky actions: changing the sign-in email or the password, adding or removing a passkey, the authenticator app,
emailed codes and recovery codes, transferring or deleting an event, and removing a team member. (Sending an urgent
notification is not one: its own confirm is enough, owner Nov 21, 2026.)

The time of the last full sign-in is kept in the session (`KEY`): set on every sign-in that completes it (a password
sign-in of an account without a second factor, the second step, a passkey; `user_logged_in` and
`twofactor.mark_verified`) and on every successful re-check at **Confirm it's you** (`console:confirm_identity`,
console/views_auth.py: a passkey, or the password plus the second step where the account has one; rate limited
like sign-in). Without a recent one, `require()` answers instead of the view: a redirect to that page that comes back
to where the person was, or 403 JSON `{"error", "reauth"}` for fetch requests (the Ask Attnd chat shows a card).
Ask Attnd checks the person's own request before releasing a confirmed action in `GUARDED_ACTIONS`
(agent/views.py `confirm`), and its internal requests run the same views, which check the same session: the agent
can never get past it.
"""

import time
from urllib.parse import urlencode

from django.contrib.auth.signals import user_logged_in
from django.dispatch import receiver
from django.http import JsonResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme

KEY = '_auth_at'
WINDOW = 20 * 60  # seconds
MESSAGE = 'Confirm it’s you first: this change needs a sign-in from the last 20 minutes.'

# Ask Attnd actions that pass the check before they run (the always-ask ones on the risky list).
GUARDED_ACTIONS = {'delete_event', 'remove_team_member', 'transfer_event_ownership'}


def mark(request):
    if request is not None and hasattr(request, 'session'):
        request.session[KEY] = time.time()


def is_recent(request, now=None):
    at = getattr(request, 'session', {}).get(KEY) if request is not None else None
    if not isinstance(at, (int, float)):
        return False
    return 0 <= (now or time.time()) - at <= WINDOW


def minutes_left(request):
    at = request.session.get(KEY)
    if not isinstance(at, (int, float)):
        return 0
    return max(0, int((WINDOW - (time.time() - at)) // 60))


def confirm_url(next_url):
    return reverse('confirm_identity') + '?' + urlencode({'next': next_url})


def _back(request, fallback):
    """Where the person was: the page the risky form was on (same site), else `fallback`, else this URL."""
    if request.method == 'GET':
        return request.get_full_path()
    referer = request.META.get('HTTP_REFERER', '')
    if referer and url_has_allowed_host_and_scheme(referer, allowed_hosts={request.get_host()},
                                                   require_https=request.is_secure()):
        from urllib.parse import urlsplit
        parts = urlsplit(referer)
        return parts.path + (f'?{parts.query}' if parts.query else '')
    return fallback or request.path


def wants_json(request):
    return ('application/json' in request.headers.get('Accept', '')
            or request.headers.get('X-Attnd-Component') or request.content_type == 'application/json')


def require(request, fallback=''):
    """None when the person signed in recently, else the response that sends them to Confirm it's you."""
    if is_recent(request):
        return None
    url = confirm_url(_back(request, fallback))
    if wants_json(request) or getattr(request, 'agent_session', None) is not None:
        return JsonResponse({'error': MESSAGE, 'reauth': url}, status=403)
    from django.contrib import messages
    messages.info(request, MESSAGE)
    return redirect(url)


def recent_auth_required(view):
    """A view that is risky as a whole (deleting an event)."""
    from functools import wraps

    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if request.method == 'POST':
            response = require(request)
            if response is not None:
                return response
        return view(request, *args, **kwargs)
    return wrapper


@receiver(user_logged_in)
def _on_sign_in(sender, request, user, **kwargs):
    """A sign-in that is complete counts; one that still waits for its second step doesn't (mark_verified does)."""
    if request is None or not hasattr(request, 'session'):
        return
    from console import twofactor
    if twofactor.has_second_factor(user):
        request.session.pop(KEY, None)
    else:
        mark(request)
