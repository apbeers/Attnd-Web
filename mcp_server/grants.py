"""MCP grants: who an access token acts for, and when that ends (docs/mcp_plan.md).

The token is django-oauth-toolkit's (hashed at rest, an hour long, its refresh token rotated on every use with reuse
detection). On top of it a grant gives the connection a person, its own signed-in session and limits:

* **Its own session.** Made when the client first exchanges its code, signed in as the person with the two-factor
  step done (they just signed in to approve it). The runner uses it like the chat uses the browser's session, so
  every console check applies unchanged. It doesn't carry a recent sign-in: actions that need one (delete an
  event, transfer it, remove a member) only run when the person confirms them in the browser.
* **Checked on every call.** The person must still be active and have `mcp_server.use_mcp` (staff), the session must
  still be theirs (a password change or sign-out everywhere ends it), and the grant must be younger than
  ``MCP_GRANT_MAX_DAYS``. Any failure revokes the grant: the client has to connect again.
"""

import hashlib
import logging
from dataclasses import dataclass
from datetime import timedelta
from importlib import import_module
from urllib.parse import urlsplit

from django.conf import settings
from django.contrib.auth import BACKEND_SESSION_KEY, HASH_SESSION_KEY, SESSION_KEY
from django.utils import timezone

from mcp_server.models import McpGrant

logger = logging.getLogger('attnd.mcp')

PERMISSION = 'mcp_server.use_mcp'
TOUCH_EVERY = timedelta(minutes=1)  # last_used_at is written at most this often


@dataclass
class Principal:
    user: object
    grant: McpGrant


def _engine():
    return import_module(settings.SESSION_ENGINE)


def max_age():
    return timedelta(days=settings.MCP_GRANT_MAX_DAYS)


def new_session(user):
    """A session signed in as `user` with the second step done, lasting as long as a grant may."""
    from console import twofactor
    session = _engine().SessionStore()
    session[SESSION_KEY] = str(user.pk)
    session[BACKEND_SESSION_KEY] = settings.AUTHENTICATION_BACKENDS[0]
    session[HASH_SESSION_KEY] = user.get_session_auth_hash()
    session[twofactor.SESSION_KEY] = str(user.pk)
    session['_attnd_mcp'] = True
    session.set_expiry(int(max_age().total_seconds()))
    session.create()
    return session.session_key


def start(user, application, token_family, redirect_uri=''):
    """The client exchanged its code: a new grant with its own session."""
    return McpGrant.objects.create(
        user=user, application=application, token_family=token_family, session_key=new_session(user),
        client_name=(application.name or urlsplit(application.client_id).hostname or 'MCP client')[:120],
        redirect_host=(urlsplit(redirect_uri).hostname or '')[:255])


def revoke(grant, reason='revoked'):
    """End a grant: its tokens and its session go."""
    from oauth2_provider.models import get_access_token_model, get_refresh_token_model
    if grant.revoked_at is None:
        grant.revoked_at = timezone.now()
        grant.revoked_reason = reason[:120]
        grant.save(update_fields=['revoked_at', 'revoked_reason'])
    RefreshToken, AccessToken = get_refresh_token_model(), get_access_token_model()
    RefreshToken.revoke_family(grant.token_family)
    tokens = RefreshToken.objects.filter(token_family=grant.token_family)
    AccessToken.objects.filter(refresh_token__in=tokens).delete()
    AccessToken.objects.filter(source_refresh_token__in=tokens).delete()
    _engine().SessionStore(session_key=grant.session_key).delete()
    logger.info('mcp grant %s revoked (%s)', grant.pk, reason)


def problem(grant):
    """Why this grant can't be used any more, or ''."""
    if grant.revoked_at is not None:
        return grant.revoked_reason or 'revoked'
    user = grant.user
    if not user.is_active:
        return 'account inactive'
    if not user.has_perm(PERMISSION):
        return 'no MCP permission'
    if grant.created_at < timezone.now() - max_age():
        return 'reached its maximum age'
    session = _engine().SessionStore(session_key=grant.session_key)
    data = session.load() if session.exists(grant.session_key) else {}
    if data.get(SESSION_KEY) != str(user.pk) or data.get(HASH_SESSION_KEY) != user.get_session_auth_hash():
        return 'signed out (password changed or signed out everywhere)'
    return ''


def usable(grant):
    """True when the grant can be used; revokes it (and says why in the log) when it can't."""
    reason = problem(grant)
    if reason and grant.revoked_at is None:
        revoke(grant, reason)
    return not reason


def for_family(token_family):
    return McpGrant.objects.select_related('user', 'application').filter(token_family=token_family).first()


def _family(access_token):
    for name in ('refresh_token', 'source_refresh_token'):
        try:
            refresh = getattr(access_token, name)
        except Exception:  # RelatedObjectDoesNotExist
            refresh = None
        if refresh is not None and refresh.token_family:
            return refresh.token_family
    return None


def _resource_ok(access_token):
    """RFC 8707: a token bound to a resource must be bound to this server's MCP URL."""
    wanted = settings.MCP_RESOURCE_URL.rstrip('/')
    resources = access_token.resource or []
    return not resources or any(str(r).rstrip('/') == wanted for r in resources)


def principal(raw_token):
    """The person and grant behind a bearer token, or None (unknown, expired, revoked, wrong server)."""
    from oauth2_provider.models import get_access_token_model
    if not raw_token or len(raw_token) > 512:
        return None
    checksum = hashlib.sha256(raw_token.encode('utf-8')).hexdigest()
    token = (get_access_token_model().objects.select_related('user', 'application')
             .filter(token_checksum=checksum).first())
    if token is None or token.is_expired() or token.user_id is None or not _resource_ok(token):
        return None
    if 'mcp' not in (token.scope or '').split():
        return None
    family = _family(token)
    grant = for_family(family) if family else None
    if grant is None or grant.user_id != token.user_id or not usable(grant):
        return None
    now = timezone.now()
    if grant.last_used_at is None or now - grant.last_used_at > TOUCH_EVERY:
        McpGrant.objects.filter(pk=grant.pk).update(last_used_at=now)
    return Principal(user=grant.user, grant=grant)
