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
    Recovery codes are stored only as SHA-256 hashes (they are random, so a plain hash is enough).
    """
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='two_factor')
    secret = models.CharField(max_length=64)
    confirmed = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    last_used_step = models.BigIntegerField(null=True, blank=True)  # TOTP time step last accepted
    last_used_at = models.DateTimeField(null=True, blank=True)
    recovery_codes = models.JSONField(default=list, blank=True)  # SHA-256 hex of each unused code

    def __str__(self):
        return f'2FA for {self.user.email or self.user.username}{"" if self.confirmed else " (not confirmed)"}'
