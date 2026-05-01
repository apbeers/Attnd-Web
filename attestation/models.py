"""
Stores the result of a successful platform attestation so we can:
  - Prevent replay of the same attestation object
  - Enforce per-device rate limits
  - Audit which devices have ever been granted access
"""

from django.db import models
from django.utils import timezone


class AttestedDevice(models.Model):
    PLATFORM_IOS = 'ios'
    PLATFORM_ANDROID = 'android'
    PLATFORM_CHOICES = [
        (PLATFORM_IOS, 'iOS'),
        (PLATFORM_ANDROID, 'Android'),
    ]

    # For iOS this is the App Attest key_id (base64url).
    # For Android this is the device_id UUID from the app.
    device_key = models.CharField(max_length=512, unique=True, db_index=True)
    platform = models.CharField(max_length=16, choices=PLATFORM_CHOICES)

    # Stored public key (iOS only) — used to verify subsequent assertions.
    public_key_pem = models.TextField(blank=True)

    # Sign counter (iOS only) — must increase monotonically to prevent replay.
    sign_count = models.PositiveBigIntegerField(default=0)

    # Lowest Play Integrity verdict seen (Android) — informational.
    android_verdict = models.CharField(max_length=64, blank=True)

    created_at = models.DateTimeField(default=timezone.now)
    last_seen = models.DateTimeField(default=timezone.now)

    class Meta:
        verbose_name = 'Attested Device'
        verbose_name_plural = 'Attested Devices'

    def __str__(self):
        return f"{self.platform}:{self.device_key[:24]}…"

    def touch(self):
        self.last_seen = timezone.now()
        self.save(update_fields=['last_seen'])
