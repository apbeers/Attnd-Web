"""How long a sign-in lasts.

- **Organizers** (owner, Nov 21, 2026): 14 days at most, sliding: every use renews it (at most once an hour,
  `SessionLifetimeMiddleware`), and it ends after 7 days without use. The sign-in time is kept in the session
  (`START_KEY`); a renewal never reaches past 14 days after it, and a request after that signs the person out.
- **Staff and superusers**: until midnight in their own time zone ("everyone just signs in once per day"):
  `console/static/console/tz.js` (on every page with a sign-in form, and the console) stores the browser's zone in the
  `attnd_tz` cookie; every successful sign-in (password, passkey, the second step, a new account) sets the session to
  expire at the next local midnight there (`request.session.set_expiry(datetime)`), or the one after it when that is
  under an hour away. An unknown or missing zone falls back to America/Chicago.

An explicit expiry also overrides SESSION_EXPIRE_AT_BROWSER_CLOSE, and it is never longer than SESSION_COOKIE_AGE
(two weeks). The second step's new session id (`twofactor.mark_verified`, `cycle_key`) keeps all of it.
"""

import time as _time
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.contrib.auth import logout
from django.contrib.auth.signals import user_logged_in
from django.dispatch import receiver
from django.utils import timezone

COOKIE = 'attnd_tz'
DEFAULT_ZONE = 'America/Chicago'
MIN_LEFT = timedelta(hours=1)
MAX_AGE = timedelta(days=14)  # organizers: from the sign-in, whatever happens
IDLE = timedelta(days=7)  # organizers: without a request
RENEW_EVERY = timedelta(hours=1)  # a sliding renewal writes the session at most this often
START_KEY = '_signed_in_at'
RENEWED_KEY = '_session_renewed_at'


def zone_for(name):
    name = (name or '').strip()
    if not name or len(name) > 64 or '..' in name or name.startswith('/'):
        return ZoneInfo(DEFAULT_ZONE)
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        return ZoneInfo(DEFAULT_ZONE)


def next_midnight(zone_name, now=None):
    """The next local midnight in that zone as an aware datetime (UTC), at least an hour away."""
    tz = zone_for(zone_name)
    now = (now or timezone.now()).astimezone(tz)
    day = now.date() + timedelta(days=1)
    while True:
        midnight = datetime.combine(day, time(0, 0), tzinfo=tz)
        if midnight - now >= MIN_LEFT:
            return midnight.astimezone(ZoneInfo('UTC'))
        day += timedelta(days=1)


def daily(user):
    """Staff and superusers sign in once a day; organizers get the 14-day sliding session."""
    return bool(getattr(user, 'is_staff', False) or getattr(user, 'is_superuser', False))


def idle_seconds(started, now):
    """Seconds the session may stay idle from now: 7 days, but never past 14 days after the sign-in."""
    left = MAX_AGE.total_seconds() - (now - started)
    return int(max(1, min(IDLE.total_seconds(), left)))


def apply(request, user=None):
    from urllib.parse import unquote
    if request is None or not hasattr(request, 'session'):
        return
    user = user or getattr(request, 'user', None)
    now = _time.time()
    request.session[START_KEY] = now
    request.session[RENEWED_KEY] = now
    if daily(user):
        request.session.set_expiry(next_midnight(unquote(request.COOKIES.get(COOKIE, ''))))
    else:
        request.session.set_expiry(int(IDLE.total_seconds()))


@receiver(user_logged_in)
def _on_sign_in(sender, request, user, **kwargs):
    apply(request, user)


class SessionLifetimeMiddleware:
    """Organizers' sliding session: renew the idle expiry on use (at most once an hour, so a busy console doesn't
    write its session on every request) and sign out once 14 days have passed since the sign-in. Staff keep their
    fixed midnight expiry. Ask Attnd's internal requests (a read-only copy of the session) are left alone."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, 'user', None)
        if (user is not None and user.is_authenticated and not daily(user)
                and getattr(request, 'agent_session', None) is None):
            now = _time.time()
            started = request.session.get(START_KEY)
            if not isinstance(started, (int, float)):
                # A session from before this rule: count from now.
                request.session[START_KEY] = started = now
            if now - started >= MAX_AGE.total_seconds():
                logout(request)
            elif now - (request.session.get(RENEWED_KEY) or 0) >= RENEW_EVERY.total_seconds():
                request.session[RENEWED_KEY] = now
                request.session.set_expiry(idle_seconds(started, now))
        return self.get_response(request)
