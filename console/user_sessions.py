"""Where an account is signed in, and "New sign-in to Attnd" emails (owner, Nov 21, 2026).

- Every sign-in (`user_logged_in`) makes a `UserSession` row: the session key, the browser (User-Agent) and a hash of
  the browser's long-lived `attnd_device` cookie (random, two years, HttpOnly; set here when missing). No place is
  stored. `SessionTrackingMiddleware` keeps the row's key in step with the session (the second step gives the session
  a new key) and its last-active time, written at most every 10 minutes.
- Account → **Signed in on other devices** lists the account's live sessions (browser and system from the
  User-Agent, last active, "This device"), signs one out, or **Sign out everywhere else** (the Django sessions are
  deleted, so those browsers are signed out on their next request).
- A sign-in from a browser this account hasn't used before (its device hash not in `SignInSecurity.known_devices`)
  emails "New sign-in to Attnd" with the time, the browser and a link to the Account page, once the account has an
  earlier device (not for a brand-new account) and only for real browsers (a User-Agent; test and CI clients have
  none or say attnd-ci). Mail goes through the normal backend, so EMAIL_ALLOWED_RECIPIENTS applies.
- `purge()` (run_scheduled_tasks) deletes rows whose session has ended.
"""

import hashlib
import logging
import secrets
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.signals import user_logged_in
from django.contrib.sessions.models import Session
from django.dispatch import receiver
from django.utils import timezone

logger = logging.getLogger(__name__)

COOKIE = 'attnd_device'
COOKIE_AGE = 2 * 365 * 24 * 3600
ROW_KEY = '_user_session'
TOUCH_EVERY = timedelta(minutes=10)
MAX_KNOWN = 30


def device_hash(token):
    return hashlib.sha256(token.encode()).hexdigest() if token else ''


def _token(request):
    token = request.COOKIES.get(COOKIE, '')
    if not (16 <= len(token) <= 64 and token.replace('-', '').replace('_', '').isalnum()):
        token = secrets.token_urlsafe(24)
        request._attnd_device_token = token  # set on the response (SessionTrackingMiddleware)
    return token


def describe(user_agent):
    """'Safari on iPhone', 'Chrome on Windows', 'Firefox on Linux'… ('Unknown browser' without one)."""
    ua = user_agent or ''
    browser = next((name for needle, name in (
        ('Edg/', 'Edge'), ('OPR/', 'Opera'), ('SamsungBrowser', 'Samsung Internet'), ('Firefox/', 'Firefox'),
        ('FxiOS', 'Firefox'), ('CriOS', 'Chrome'), ('Chrome/', 'Chrome'), ('Safari/', 'Safari')) if needle in ua), '')
    system = next((name for needle, name in (
        ('iPhone', 'iPhone'), ('iPad', 'iPad'), ('Android', 'Android'), ('CrOS', 'ChromeOS'), ('Windows', 'Windows'),
        ('Macintosh', 'Mac'), ('Linux', 'Linux')) if needle in ua), '')
    if browser and system:
        return f'{browser} on {system}'
    return browser or system or 'Unknown browser'


def _real_browser(user_agent):
    ua = (user_agent or '').lower()
    return bool(ua) and 'attnd-ci' not in ua and 'python-requests' not in ua


@receiver(user_logged_in)
def _on_sign_in(sender, request, user, **kwargs):
    if request is None or not hasattr(request, 'session') or getattr(request, 'agent_session', None) is not None:
        return
    from console.models import UserSession
    from console.twofactor import security
    if request.session.session_key is None:
        request.session.save()
    ua = request.META.get('HTTP_USER_AGENT', '')[:300]
    token = _token(request)
    hashed = device_hash(token)
    row = UserSession.objects.create(user=user, session_key=request.session.session_key, device=hashed,
                                     user_agent=ua)
    request.session[ROW_KEY] = row.pk
    sec = security(user)
    known = list(sec.known_devices or [])
    if hashed in known:
        known.remove(hashed)
    elif known and _real_browser(ua):
        _email_new_sign_in(user, ua)
    sec.known_devices = (known + [hashed])[-MAX_KNOWN:]
    sec.save(update_fields=['known_devices'])


def _email_new_sign_in(user, user_agent):
    if not user.email:
        return
    from django.core.mail import send_mail

    from console.mail import site_url
    from django.urls import reverse
    when = timezone.now().strftime('%b %-d, %Y at %H:%M UTC')
    try:
        send_mail(
            'New sign-in to Attnd',
            f'Your Attnd account ({user.email}) was just signed in on a new device:\n\n'
            f'    {describe(user_agent)}\n    {when}\n\n'
            'If this was you, there is nothing to do.\n\n'
            'If it wasn’t, sign that device out and change your password on your Account page:\n'
            f'{site_url(reverse("console:account"))}#sessions\n',
            settings.DEFAULT_FROM_EMAIL, [user.email])
    except Exception:  # a sign-in never fails because of the email
        logger.exception('Could not send the new sign-in email')


class SessionTrackingMiddleware:
    """Keeps the account's UserSession row in step: its session key (after the second step's new key) and its
    last-active time (at most every 10 minutes); sets the device cookie when a sign-in made one. Skips Ask Attnd's
    internal requests."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        token = getattr(request, '_attnd_device_token', None)
        if token:
            response.set_cookie(COOKIE, token, max_age=COOKIE_AGE, httponly=True, samesite='Lax',
                                secure=settings.SESSION_COOKIE_SECURE)
        user = getattr(request, 'user', None)
        if (user is None or not user.is_authenticated or getattr(request, 'agent_session', None) is not None
                or not hasattr(request, 'session')):
            return response
        try:
            self._touch(request, user)
        except Exception:
            logger.exception('Could not update the signed-in session')
        return response

    def _touch(self, request, user):
        from console.models import UserSession
        key = request.session.session_key
        if not key:
            return
        row_id = request.session.get(ROW_KEY)
        row = UserSession.objects.filter(pk=row_id, user=user).first() if row_id else None
        now = timezone.now()
        if row is None:  # a session from before this was tracked
            row = UserSession.objects.create(user=user, session_key=key,
                                             device=device_hash(request.COOKIES.get(COOKIE, '')),
                                             user_agent=request.META.get('HTTP_USER_AGENT', '')[:300])
            request.session[ROW_KEY] = row.pk
            return
        changes = {}
        if row.session_key != key:
            changes['session_key'] = key
        if now - row.last_active >= TOUCH_EVERY:
            changes['last_active'] = now
        if changes:
            UserSession.objects.filter(pk=row.pk).update(**changes)


def live_sessions(user):
    """This account's sessions that are still signed in, newest activity first."""
    from console.models import UserSession
    rows = list(UserSession.objects.filter(user=user))
    alive = set(Session.objects.filter(session_key__in=[r.session_key for r in rows], expire_date__gt=timezone.now())
                .values_list('session_key', flat=True))
    return [r for r in rows if r.session_key in alive]


def sign_out(user, rows):
    """Sign these sessions out (their browsers see the sign-in page on the next request)."""
    from console.models import UserSession
    keys = [r.session_key for r in rows]
    Session.objects.filter(session_key__in=keys).delete()
    UserSession.objects.filter(user=user, pk__in=[r.pk for r in rows]).delete()
    return len(keys)


def purge():
    """Rows of sessions that have ended (signed out, expired, deleted)."""
    from console.models import UserSession
    alive = Session.objects.filter(expire_date__gt=timezone.now()).values('session_key')
    deleted, _ = UserSession.objects.exclude(session_key__in=alive).delete()
    return deleted
