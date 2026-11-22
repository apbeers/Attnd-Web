"""The owner's platform settings (D2, owner Nov 21, 2026): prices, the free phone limit, upgrade requests, manual
payment records and per-event overrides. Every change through the Platform admin page always asks and is logged in
History (credit.history)."""
from decimal import Decimal

from django.conf import settings
from django.contrib.auth.models import User
from django.db import models
from django.utils import timezone


class PlatformSettings(models.Model):
    """Singleton (pk 1): used everywhere a price or the free limit shows."""
    standard_price_usd = models.DecimalField('Standard plan price (USD)', max_digits=8, decimal_places=2, default=299)
    studio_price_usd = models.DecimalField('Studio plan price (USD)', max_digits=8, decimal_places=2, default=499)
    free_device_limit = models.PositiveIntegerField('Free phones per unpaid event', default=20)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')

    class Meta:
        verbose_name = verbose_name_plural = 'Platform settings'
        # In no role (attnd.permissions): superusers only (batch 7 Track E).
        permissions = [('view_architecture', 'See the architecture diagram, its live status and the app options')]

    def __str__(self):
        return 'Platform settings'

    @classmethod
    def load(cls):
        from django.core.cache import caches
        defaults = {'free_device_limit': int(getattr(settings, 'FREE_DEVICE_LIMIT', 20))}
        if getattr(settings, 'TESTING', False):
            return cls.objects.filter(pk=1).first() or cls(pk=1, **defaults)
        cache = caches['local']
        found = cache.get('platform_settings')
        if found is None:
            found, _ = cls.objects.get_or_create(pk=1, defaults=defaults)
            cache.set('platform_settings', found, 30)
        return found

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        from django.core.cache import caches
        caches['local'].delete('platform_settings')


class UpgradeRequest(models.Model):
    """"Remove the limit" (Standard) or "Upgrade to publish" (Studio): no checkout yet, the owner approves by hand."""
    STANDARD, STUDIO = 'standard', 'studio'
    KINDS = [(STANDARD, 'Standard (remove the limit)'), (STUDIO, 'Studio (publish the website)')]
    PENDING, APPROVED, DECLINED = 'pending', 'approved', 'declined'
    STATUSES = [(PENDING, 'Pending'), (APPROVED, 'Approved'), (DECLINED, 'Declined')]
    event = models.ForeignKey('events.Event', on_delete=models.CASCADE, related_name='upgrade_requests')
    requested_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='+')
    kind = models.CharField(max_length=10, choices=KINDS)
    price_usd = models.DecimalField(max_digits=8, decimal_places=2, default=Decimal('0'))
    message = models.TextField(blank=True)
    status = models.CharField(max_length=10, choices=STATUSES, default=PENDING, db_index=True)
    created_at = models.DateTimeField(default=timezone.now)
    decided_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    decided_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.get_kind_display()} for {self.event} ({self.status})'


class PaymentRecord(models.Model):
    """A payment recorded by hand (no checkout yet; Stripe later)."""
    event = models.ForeignKey('events.Event', on_delete=models.CASCADE, related_name='payments')
    plan = models.CharField(max_length=10, choices=UpgradeRequest.KINDS)
    amount_usd = models.DecimalField(max_digits=8, decimal_places=2)
    paid_at = models.DateTimeField(default=timezone.now)
    recorded_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='+')
    note = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ['-paid_at']

    def __str__(self):
        return f'{self.event} {self.plan} ${self.amount_usd}'


class EventOverride(models.Model):
    """What the owner set for one event, applied by the checks (free_limit, Studio publishing and caps)."""
    event = models.OneToOneField('events.Event', on_delete=models.CASCADE, related_name='override')
    free_device_limit = models.PositiveIntegerField('Free phones (empty = the platform default)', null=True, blank=True)
    no_free_limit = models.BooleanField('No free limit', default=False)
    allow_publishing = models.BooleanField('Allow publishing the website without the Studio plan', default=False)
    studio_daily_cap_usd = models.DecimalField('Studio AI per day (USD, empty = default)', max_digits=8,
                                               decimal_places=2, null=True, blank=True)
    notes = models.TextField(blank=True)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')

    def __str__(self):
        return f'Overrides for {self.event}'

    @classmethod
    def of(cls, event):
        """The event's override or None (one query; cached on the event)."""
        if not hasattr(event, '_override_cache'):
            event._override_cache = cls.objects.filter(event_id=event.pk).first() if event.pk else None
        return event._override_cache


class Invoice(models.Model):
    """One bill for an event (batch 7 billing): made when an upgrade is approved, a payment is recorded by hand, or
    Pay now starts a Stripe Checkout. Numbers are sequential per year (ATT-2026-0001) and never reused."""
    OPEN, PAID, VOID = 'open', 'paid', 'void'
    STATUSES = [(OPEN, 'Open'), (PAID, 'Paid'), (VOID, 'Void')]
    MANUAL, STRIPE = 'manual', 'stripe'
    METHODS = [(MANUAL, 'Recorded by Attnd'), (STRIPE, 'Card (Stripe)')]
    number = models.CharField(max_length=20, unique=True)
    event = models.ForeignKey('events.Event', on_delete=models.CASCADE, related_name='invoices')
    plan = models.CharField(max_length=10, choices=UpgradeRequest.KINDS)
    description = models.CharField(max_length=200)
    amount_usd = models.DecimalField(max_digits=8, decimal_places=2)
    status = models.CharField(max_length=6, choices=STATUSES, default=OPEN)
    method = models.CharField(max_length=6, choices=METHODS, default=MANUAL)
    billed_to = models.CharField(max_length=254, blank=True)  # the owner's email when issued
    issued_at = models.DateTimeField(default=timezone.now)
    paid_at = models.DateTimeField(null=True, blank=True)
    payment = models.OneToOneField(PaymentRecord, on_delete=models.SET_NULL, null=True, blank=True, related_name='invoice')
    stripe_session = models.CharField(max_length=255, blank=True)
    stripe_customer = models.CharField(max_length=255, blank=True)
    note = models.CharField(max_length=200, blank=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')

    class Meta:
        ordering = ['-issued_at', '-pk']

    def __str__(self):
        return f'{self.number} · {self.event} · ${self.amount_usd}'
