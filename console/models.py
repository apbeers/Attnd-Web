from django.contrib.auth.models import User
from django.db import models

from events.models import Event


class Order(models.Model):
    order_date = models.DateTimeField(auto_now_add=True)
    order_id = models.CharField(max_length=256)
    event = models.OneToOneField(Event, on_delete=models.DO_NOTHING)
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    refunded = models.BooleanField(default=False)

    def __str__(self):
        return f'{self.event.name} - {self.user.username}'


class TwoFactorDevice(models.Model):
    """An authenticator app (TOTP, RFC 6238) for one account. See console/twofactor.py.

    The secret is stored as base32 text, not encrypted: encrypting it at rest would need a third-party
    crypto library, and a key kept next to it (env) would add little. Database access alone isn't enough
    to sign in (the password is still needed), and `reset_2fa` / deleting this row recovers a lockout.
    Recovery codes live on SignInSecurity (they work with every second factor).
    """
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='two_factor')
    secret = models.CharField(max_length=64)
    confirmed = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    last_used_step = models.BigIntegerField(null=True, blank=True)  # TOTP time step last accepted
    last_used_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f'2FA for {self.user.email or self.user.username}{"" if self.confirmed else " (not confirmed)"}'


class SignInSecurity(models.Model):
    """Per-account sign-in settings that aren't tied to one method (console/twofactor.py, passkeys.py):
    the WebAuthn user handle, recovery codes, emailed codes and the "add a passkey" prompt.

    - `user_handle`: random 32 bytes (base64url) given to authenticators as WebAuthn user.id; a passkey
      sign-in finds the account by it. Not the database id or the email, which the authenticator would store.
    - `recovery_codes`: SHA-256 hex of each unused recovery code (they are random, so a plain hash is
      enough). Optional, made from the Account page; they work with any second factor.
    - Emailed codes: only an HMAC (keyed with SECRET_KEY) of the current 6-digit code and its expiry;
      a new code replaces the old one, and a used code is cleared.
    """
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='sign_in_security')
    user_handle = models.CharField(max_length=64, unique=True)
    recovery_codes = models.JSONField(default=list, blank=True)
    email_codes_enabled = models.BooleanField(default=False)
    email_code_hash = models.CharField(max_length=64, blank=True)
    email_code_expires_at = models.DateTimeField(null=True, blank=True)
    passkey_prompt_dismissed = models.BooleanField(default=False)

    class Meta:
        verbose_name = 'sign-in settings'
        verbose_name_plural = 'sign-in settings'

    def __str__(self):
        return f'Sign-in settings for {self.user.email or self.user.username}'


class Passkey(models.Model):
    """A WebAuthn credential (passkey) of one account, verified by console/webauthn.py.

    `public_key` is the COSE_Key exactly as the authenticator sent it. `sign_count` is the last signature
    counter (0 for most synced passkeys, which don't count). `backup_eligible` / `backed_up` are the BE/BS
    flags: a backed-up passkey is synced (iCloud Keychain, Google Password Manager …), so losing one device
    doesn't lose it.
    """
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='passkeys')
    credential_id = models.CharField(max_length=1400, unique=True)  # base64url, up to 1023 bytes
    public_key = models.BinaryField()
    sign_count = models.BigIntegerField(default=0)
    transports = models.JSONField(default=list, blank=True)
    aaguid = models.CharField(max_length=32, blank=True)
    name = models.CharField(max_length=64)
    backup_eligible = models.BooleanField(default=False)
    backed_up = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['created_at']

    def __str__(self):
        return f'Passkey “{self.name}” for {self.user.email or self.user.username}'


class UndoEntry(models.Model):
    """One change someone made (a Save, a delete, an Ask Attnd step), recorded row by row so exactly that change can
    be undone for 24 hours without touching other people's later edits (console/undo.py).

    operations = [{model, pk, op: create|update|delete, before: serialized row | None, after: {field: value},
    fields: [changed field names], related: {...}}], in the order they happened."""
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='undo_entries')
    event = models.ForeignKey(Event, on_delete=models.CASCADE, null=True, blank=True, related_name='+')
    label = models.CharField(max_length=300)
    operations = models.JSONField(default=list)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)
    # The entry this one reverses (an undo is recorded too, so it can be redone).
    reverses = models.ForeignKey('self', on_delete=models.SET_NULL, null=True, blank=True, related_name='+')

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['user', '-created_at'])]

    def __str__(self):
        return f'Undo {self.pk}: {self.label[:60]}'
