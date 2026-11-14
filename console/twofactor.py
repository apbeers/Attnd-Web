"""Two-factor sign-in (TOTP authenticator app + single-use recovery codes).

How it works:
- Signing in with the password logs the account in as usual (any login path: sign-in page, signup,
  tests' force_login …). Every new login clears the session's "second step done" flag
  (`user_logged_in` signal below).
- `console.middleware.TwoFactorMiddleware` keeps a logged-in account out of the console, the Django
  admin and the password-change pages until the flag is set: accounts with a confirmed device are sent
  to the code prompt (`two_factor_verify`), staff and superusers without one to enrollment
  (`two_factor_setup`). Accounts without a device that aren't staff are not affected.
- Wrong codes are rate limited per account (RATE_LIMITS['otp_user']); a TOTP step is accepted once.
"""

import hashlib
import secrets

from django.contrib.auth.signals import user_logged_in
from django.db import transaction
from django.dispatch import receiver
from django.utils import timezone

from console import totp
from console.models import TwoFactorDevice

SESSION_KEY = '_two_factor_user'
RECOVERY_CODE_COUNT = 10


def is_required(user) -> bool:
    """Staff and superusers must use two-factor sign-in."""
    return bool(user.is_staff or user.is_superuser)


def confirmed_device(user):
    if not user.is_authenticated:
        return None
    return TwoFactorDevice.objects.filter(user=user, confirmed=True).first()


def is_verified(request) -> bool:
    return request.user.is_authenticated and request.session.get(SESSION_KEY) == str(request.user.pk)


def mark_verified(request) -> None:
    request.session[SESSION_KEY] = str(request.user.pk)
    request.session.cycle_key()  # new session id once fully signed in


def pending_step(request):
    """'verify', 'setup' or None: what the signed-in account still has to do before using the console."""
    user = request.user
    if not user.is_authenticated or is_verified(request):
        return None
    if confirmed_device(user) is not None:
        return 'verify'
    if is_required(user):
        return 'setup'
    return None


@receiver(user_logged_in)
def _clear_on_login(sender, request, user, **kwargs):
    if request is not None and hasattr(request, 'session'):
        request.session.pop(SESSION_KEY, None)


# ---------------------------------------------------------------------------
# Recovery codes
# ---------------------------------------------------------------------------

def _normalize_recovery(code: str) -> str:
    return ''.join(ch for ch in str(code).lower() if ch.isalnum())


def _hash_recovery(code: str) -> str:
    return hashlib.sha256(_normalize_recovery(code).encode()).hexdigest()


def new_recovery_codes(device: TwoFactorDevice) -> list[str]:
    """Replace the device's recovery codes; returns the plain codes (shown once, never stored)."""
    codes = [f'{c[:5]}-{c[5:]}' for c in (secrets.token_hex(5) for _ in range(RECOVERY_CODE_COUNT))]
    device.recovery_codes = [_hash_recovery(c) for c in codes]
    device.save(update_fields=['recovery_codes'])
    return codes


# ---------------------------------------------------------------------------
# Checking codes
# ---------------------------------------------------------------------------

def check_code(device: TwoFactorDevice, code: str, allow_recovery: bool = True) -> str | None:
    """Accept a 6-digit code (once per time step) or an unused recovery code (then used up).
    Returns 'totp' or 'recovery' when accepted, None otherwise. Locks the row, so two requests can't
    use the same code at once."""
    code = (code or '').strip()
    if not code:
        return None
    with transaction.atomic():
        locked = TwoFactorDevice.objects.select_for_update().get(pk=device.pk)
        step = totp.match_step(locked.secret, code, after_step=locked.last_used_step)
        if step is not None:
            locked.last_used_step = step
            locked.last_used_at = timezone.now()
            locked.save(update_fields=['last_used_step', 'last_used_at'])
            result = 'totp'
        elif allow_recovery and _normalize_recovery(code) and _hash_recovery(code) in locked.recovery_codes:
            locked.recovery_codes = [h for h in locked.recovery_codes if h != _hash_recovery(code)]
            locked.last_used_at = timezone.now()
            locked.save(update_fields=['recovery_codes', 'last_used_at'])
            result = 'recovery'
        else:
            return None
    device.refresh_from_db()
    return result


def start_setup(user) -> TwoFactorDevice:
    """The account's unconfirmed device (made with a new secret if needed)."""
    device, _ = TwoFactorDevice.objects.get_or_create(user=user, defaults={'secret': totp.new_secret()})
    return device


def confirm_setup(device: TwoFactorDevice, code: str) -> list[str] | None:
    """Turn the device on if `code` matches its secret; returns the new recovery codes."""
    if check_code(device, code, allow_recovery=False) is None:
        return None
    device.confirmed = True
    device.confirmed_at = timezone.now()
    device.save(update_fields=['confirmed', 'confirmed_at'])
    return new_recovery_codes(device)
