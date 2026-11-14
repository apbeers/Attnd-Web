"""Passkey ceremonies for the views: options (challenge kept in the session, single use) and verification
against the stored Passkey rows. The WebAuthn checks themselves are in console/webauthn.py."""

import logging

from django.db import transaction
from django.utils import timezone

from console import twofactor, webauthn
from console.models import Passkey, SignInSecurity
from console.webauthn import WebAuthnError, b64url_decode, b64url_encode

logger = logging.getLogger(__name__)

MAX_PASSKEYS = 20
NAME_MAX = 64


def clean_name(name: str, user_agent: str = '') -> str:
    name = ' '.join(str(name or '').split())[:NAME_MAX]
    return name or webauthn.default_name(user_agent)


# ---------------------------------------------------------------------------
# Registration (a signed-in account adds a passkey)
# ---------------------------------------------------------------------------

def registration_options(request) -> dict:
    user = request.user
    row = twofactor.security(user)
    challenge = webauthn.new_challenge()
    rp = webauthn.rp_id(request)
    webauthn.store_challenge(request.session, 'register', challenge, user=user.pk, rp=rp)
    exclude = [webauthn.credential_descriptor(p.credential_id, p.transports)
               for p in Passkey.objects.filter(user=user)]
    name = user.email or user.username
    return webauthn.registration_options(rp=rp, user_handle=b64url_decode(row.user_handle), user_name=name,
                                         display_name=name, challenge=challenge, exclude=exclude)


def finish_registration(request, credential: dict, name: str) -> Passkey:
    state = webauthn.pop_challenge(request.session, 'register', webauthn.client_challenge(credential))
    if state is None or state.get('user') != request.user.pk:
        raise WebAuthnError('no registration in progress')
    if Passkey.objects.filter(user=request.user).count() >= MAX_PASSKEYS:
        raise WebAuthnError('too many passkeys')
    new = webauthn.verify_registration(credential, challenge=state['challenge'], rp=state['rp'],
                                       origins=webauthn.allowed_origins())
    if Passkey.objects.filter(credential_id=new.credential_id).exists():
        raise WebAuthnError('credential already registered')
    return Passkey.objects.create(
        user=request.user, credential_id=new.credential_id, public_key=new.public_key,
        sign_count=new.sign_count, transports=new.transports, aaguid=new.aaguid,
        name=clean_name(name, request.META.get('HTTP_USER_AGENT', '')),
        backup_eligible=new.backup_eligible, backed_up=new.backed_up)


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------

def authentication_options(request, user=None) -> dict:
    """Usernameless sign-in (user None: any passkey for this site; the browser offers them, also as
    autofill), or the second step of a password sign-in (only that account's passkeys)."""
    challenge = webauthn.new_challenge()
    rp = webauthn.rp_id(request)
    allow = []
    if user is not None:
        allow = [webauthn.credential_descriptor(p.credential_id, p.transports)
                 for p in Passkey.objects.filter(user=user)]
    webauthn.store_challenge(request.session, 'auth', challenge, user=user.pk if user else None, rp=rp)
    return webauthn.authentication_options(rp=rp, challenge=challenge, allow=allow)


def finish_authentication(request, credential: dict, user=None) -> Passkey:
    """Verifies the assertion and returns the passkey used (its .user is who signed in). With `user`, the
    passkey must be theirs (second step)."""
    state = webauthn.pop_challenge(request.session, 'auth', webauthn.client_challenge(credential))
    expected_user = user.pk if user is not None else None
    if state is None or state.get('user') != expected_user:
        raise WebAuthnError('no sign-in in progress')
    credential_id, user_handle = webauthn.parse_assertion(credential)
    passkey = Passkey.objects.select_related('user').filter(credential_id=credential_id).first()
    if passkey is None:
        raise WebAuthnError('unknown credential')
    if user is not None and passkey.user_id != user.pk:
        raise WebAuthnError('credential belongs to another account')
    stored_handle = SignInSecurity.objects.filter(user_id=passkey.user_id).values_list('user_handle', flat=True).first()
    if user is None and user_handle is None:
        raise WebAuthnError('no user handle')  # discoverable credentials always return it
    if user_handle is not None and (stored_handle is None or b64url_encode(user_handle) != stored_handle):
        raise WebAuthnError('user handle does not match the credential')
    if not passkey.user.is_active:
        raise WebAuthnError('account inactive')
    with transaction.atomic():
        locked = Passkey.objects.select_for_update().get(pk=passkey.pk)
        try:
            auth = webauthn.verify_authentication(credential, challenge=state['challenge'], rp=state['rp'],
                                                  origins=webauthn.allowed_origins(),
                                                  public_key=bytes(locked.public_key),
                                                  stored_sign_count=locked.sign_count)
        except WebAuthnError as exc:
            if 'sign count' in str(exc):
                logger.warning('Passkey %s of user %s rejected: sign count regression', locked.pk, locked.user_id)
            raise
        locked.sign_count = max(locked.sign_count, auth.sign_count)
        locked.backed_up = auth.backed_up
        locked.backup_eligible = auth.backup_eligible
        locked.last_used_at = timezone.now()
        locked.save(update_fields=['sign_count', 'backed_up', 'backup_eligible', 'last_used_at'])
    locked.user = passkey.user
    return locked


def can_remove(user, passkey: Passkey) -> bool:
    """Staff must keep at least one strong method (passkey or authenticator app)."""
    if not twofactor.is_required(user):
        return True
    others = Passkey.objects.filter(user=user).exclude(pk=passkey.pk).exists()
    return others or twofactor.confirmed_device(user) is not None
