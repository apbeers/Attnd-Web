"""Sign-ins last until midnight in the person's own time zone (owner, Nov 21, 2026: "everyone just signs in once
per day").

`console/static/console/tz.js` (on every page with a sign-in form, and the console) stores the browser's zone in the
`attnd_tz` cookie. Every successful sign-in (password, passkey, the second step, a new account) sets the session to
expire at the next local midnight there (`request.session.set_expiry(datetime)`), or the one after it when that is
under an hour away. An unknown or missing zone falls back to America/Chicago. An explicit expiry also overrides
SESSION_EXPIRE_AT_BROWSER_CLOSE, and it is always shorter than SESSION_COOKIE_AGE (two weeks).
"""

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.contrib.auth.signals import user_logged_in
from django.dispatch import receiver
from django.utils import timezone

COOKIE = 'attnd_tz'
DEFAULT_ZONE = 'America/Chicago'
MIN_LEFT = timedelta(hours=1)


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


def apply(request):
    from urllib.parse import unquote
    if request is None or not hasattr(request, 'session'):
        return
    request.session.set_expiry(next_midnight(unquote(request.COOKIES.get(COOKIE, ''))))


@receiver(user_logged_in)
def _expire_at_midnight(sender, request, user, **kwargs):
    apply(request)
