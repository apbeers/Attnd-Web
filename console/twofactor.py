"""Sign-in steps: passkeys (console/passkeys.py), authenticator app (TOTP), emailed codes, recovery codes.

How it works:
- A **passkey** sign-in (WebAuthn with user verification) logs the account in and counts as both steps
  at once (`mark_verified`).
- Signing in with the **password** logs the account in as usual (any login path: sign-in page, signup,
  tests' force_login …). Every new login clears the session's "second step done" flag
  (`user_logged_in` signal below).
- `console.middleware.TwoFactorMiddleware` keeps a logged-in account out of the console, the Django
  admin and the password-change pages until the flag is set: accounts with a second factor (a passkey,
  an authenticator app, or emailed codes turned on) are sent to the second step (`two_factor_verify`),
  staff and superusers without a **strong** method (passkey or authenticator app) to enrollment
  (`two_factor_setup`). Accounts without a second factor that aren't staff are not affected.
- Emailed codes are a weaker factor (whoever reads the inbox can also reset the password), so they are
  for organizers only: staff can't turn them on and they are never accepted for a staff account.
- Wrong codes are rate limited per account (RATE_LIMITS['otp_user']); a TOTP step is accepted once;
  emailed and recovery codes are single use. Recovery codes are optional (made from the Account page).
"""

import hashlib
import hmac
import secrets
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.signals import user_logged_in
from django.db import IntegrityError, transaction
from django.dispatch import receiver
from django.utils import timezone

from console import totp, webauthn
from console.models import Passkey, SignInSecurity, TwoFactorDevice

SESSION_KEY = '_two_factor_user'
RECOVERY_CODE_COUNT = 10
EMAIL_CODE_MINUTES = 10


def is_required(user) -> bool:
    """Staff and superusers must use two-factor sign-in with a strong method."""
    return bool(user.is_staff or user.is_superuser)


def security(user) -> SignInSecurity:
    """The account's SignInSecurity row (made on first use, with a new random WebAuthn user handle)."""
    try:
        return user.sign_in_security
    except SignInSecurity.DoesNotExist:
        pass
    handle = webauthn.b64url_encode(webauthn.new_user_handle())
    try:
        with transaction.atomic():
            row, _ = SignInSecurity.objects.get_or_create(user=user, defaults={'user_handle': handle})
    except IntegrityError:  # made by a concurrent request
        row = SignInSecurity.objects.get(user=user)
    user.sign_in_security = row
    return row


def confirmed_device(user):
    if not user.is_authenticated:
        return None
    return TwoFactorDevice.objects.filter(user=user, confirmed=True).first()


def has_passkeys(user) -> bool:
    return user.is_authenticated and Passkey.objects.filter(user=user).exists()


def email_codes_on(user) -> bool:
    """Emailed codes are a second factor for this account (organizers only, see the module docstring)."""
    if not user.is_authenticated or is_required(user) or not user.email:
        return False
    return SignInSecurity.objects.filter(user=user, email_codes_enabled=True).exists()


def strong_methods(user) -> list[str]:
    methods = []
    if has_passkeys(user):
        methods.append('passkey')
    if confirmed_device(user) is not None:
        methods.append('totp')
    return methods


def has_second_factor(user) -> bool:
    return bool(strong_methods(user)) or email_codes_on(user)


def is_verified(request) -> bool:
    return request.user.is_authenticated and request.session.get(SESSION_KEY) == str(request.user.pk)


def mark_verified(request) -> None:
    from console import recent_auth
    request.session[SESSION_KEY] = str(request.user.pk)
    recent_auth.mark(request)  # a full sign-in: risky actions are allowed for 20 minutes (console/recent_auth.py)
    request.session.cycle_key()  # new session id once fully signed in (keeps the midnight expiry, session_expiry)


def pending_step(request):
    """'verify', 'setup' or None: what the signed-in account still has to do before using the console."""
    user = request.user
    if not user.is_authenticated or is_verified(request):
        return None
    if is_required(user) and not strong_methods(user):
        return 'setup'
    if has_second_factor(user):
        return 'verify'
    return None


@receiver(user_logged_in)
def _clear_on_login(sender, request, user, **kwargs):
    if request is not None and hasattr(request, 'session'):
        request.session.pop(SESSION_KEY, None)


# ---------------------------------------------------------------------------
# Recovery codes (one set per account, for any second factor)
# ---------------------------------------------------------------------------
# Optional: never made automatically. The Account page offers "Set up recovery codes" (none yet) or
# "New recovery codes" once the account has a second factor, after re-checking an authenticator code or
# the password. They are accepted at the second step while the account has any.

def _normalize_recovery(code: str) -> str:
    return ''.join(ch for ch in str(code).lower() if ch.isalnum())


def _hash_recovery(code: str) -> str:
    return hashlib.sha256(_normalize_recovery(code).encode()).hexdigest()


def new_recovery_codes(user) -> list[str]:
    """Replace the account's recovery codes; returns the plain codes (shown once, never stored)."""
    codes = [f'{c[:5]}-{c[5:]}' for c in (secrets.token_hex(5) for _ in range(RECOVERY_CODE_COUNT))]
    row = security(user)
    row.recovery_codes = [_hash_recovery(c) for c in codes]
    row.save(update_fields=['recovery_codes'])
    return codes


def recovery_codes_left(user) -> int:
    row = SignInSecurity.objects.filter(user=user).first()
    return len(row.recovery_codes) if row else 0


def after_factor_removed(user) -> None:
    """No second factor left: recovery codes have nothing to recover, so they go."""
    if not has_second_factor(user):
        SignInSecurity.objects.filter(user=user).update(recovery_codes=[])


def use_recovery_code(user, code: str) -> bool:
    """Accept an unused recovery code (then used up). Locks the row, so a code can't be used twice at once."""
    if not _normalize_recovery(code):
        return False
    hashed = _hash_recovery(code)
    with transaction.atomic():
        row = SignInSecurity.objects.select_for_update().filter(user=user).first()
        if row is None or hashed not in row.recovery_codes:
            return False
        row.recovery_codes = [h for h in row.recovery_codes if h != hashed]
        row.save(update_fields=['recovery_codes'])
    user.sign_in_security = row
    return True


# ---------------------------------------------------------------------------
# Authenticator app (TOTP)
# ---------------------------------------------------------------------------

def check_totp(device: TwoFactorDevice, code: str) -> bool:
    """Accept a 6-digit code, once per time step. Locks the row, so two requests can't use one code."""
    code = (code or '').strip()
    if not code:
        return False
    with transaction.atomic():
        locked = TwoFactorDevice.objects.select_for_update().get(pk=device.pk)
        step = totp.match_step(locked.secret, code, after_step=locked.last_used_step)
        if step is None:
            return False
        locked.last_used_step = step
        locked.last_used_at = timezone.now()
        locked.save(update_fields=['last_used_step', 'last_used_at'])
    device.refresh_from_db()
    return True


def start_setup(user) -> TwoFactorDevice:
    """The account's unconfirmed device (made with a new secret if needed)."""
    device, _ = TwoFactorDevice.objects.get_or_create(user=user, defaults={'secret': totp.new_secret()})
    return device


def confirm_setup(device: TwoFactorDevice, code: str) -> bool:
    """Turn the device on if `code` matches its secret (True). No recovery codes are made here: the
    account makes them later from its Account page, if it wants them."""
    if not check_totp(device, code):
        return False
    device.confirmed = True
    device.confirmed_at = timezone.now()
    device.save(update_fields=['confirmed', 'confirmed_at'])
    return True


# ---------------------------------------------------------------------------
# Emailed codes (organizers only)
# ---------------------------------------------------------------------------

def _hash_email_code(user, code: str) -> str:
    return hmac.new(settings.SECRET_KEY.encode(), f'email-code:{user.pk}:{code}'.encode(), hashlib.sha256).hexdigest()


def send_email_code(user) -> None:
    """Email a new 6-digit code (valid EMAIL_CODE_MINUTES, single use; replaces any earlier one)."""
    code = f'{secrets.randbelow(10 ** 6):06d}'
    row = security(user)
    row.email_code_hash = _hash_email_code(user, code)
    row.email_code_expires_at = timezone.now() + timedelta(minutes=EMAIL_CODE_MINUTES)
    row.save(update_fields=['email_code_hash', 'email_code_expires_at'])
    from console import mail
    mail.send('sign_in_code', [user.email], f'Your Attnd sign-in code: {code}', heading='Your sign-in code',
              paragraphs=['Your code to finish signing in to Attnd is:'], code=code,
              after=[f'It works once, for {EMAIL_CODE_MINUTES} minutes. If you didn’t just sign in, someone else has '
                     'your password: change it on your Account page and turn on a passkey.'],
              from_email=settings.DEFAULT_FROM_EMAIL, fail_silently=False)


def check_email_code(user, code: str) -> bool:
    code = ''.join(ch for ch in str(code or '') if not ch.isspace())
    if not email_codes_on(user) or len(code) != 6 or not code.isdigit():
        return False
    with transaction.atomic():
        row = SignInSecurity.objects.select_for_update().filter(user=user).first()
        if (row is None or not row.email_code_hash or row.email_code_expires_at is None
                or row.email_code_expires_at < timezone.now()
                or not hmac.compare_digest(row.email_code_hash, _hash_email_code(user, code))):
            return False
        row.email_code_hash, row.email_code_expires_at = '', None
        row.save(update_fields=['email_code_hash', 'email_code_expires_at'])
    return True


# ---------------------------------------------------------------------------
# The second step
# ---------------------------------------------------------------------------

def check_code(user, code: str, allow_recovery: bool = True) -> str | None:
    """A code typed at the second step: 'totp', 'email' or 'recovery' when accepted, else None."""
    device = confirmed_device(user)
    if device is not None and check_totp(device, code):
        return 'totp'
    if check_email_code(user, code):
        return 'email'
    if allow_recovery and has_second_factor(user) and use_recovery_code(user, code):
        return 'recovery'
    return None
