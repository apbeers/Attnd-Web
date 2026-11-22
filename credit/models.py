"""AI credit (batch 6 §2, owner Nov 21, 2026): one ledger of every AI call and the live limits.

- ``AiSpend``: one row per AI call (Claude through attnd/claude.py, Ask Attnd's loop, Studio's providers and
  images): who (person, or a visitor before an account), which event, feature, provider / model, tokens, cost,
  request id, time, and whether it counts against a limit (``counted``; staff-only Prospects, DEMO / showcase / App
  Review maintenance and CI don't).
- ``AiLimits``: the live limits (a singleton the staff "AI limits" card edits; environment variables only give the
  first defaults): the signed-in monthly default, the visitor allowance, the platform alert amount and the safety caps.
- ``AiLimitOverride``: a person's own monthly limit, or no limit.
- ``AiCreditNotice``: the 80 % / 100 % warnings and the platform alert already sent this month (sent once).
"""
from decimal import Decimal

from django.conf import settings
from django.contrib.auth.models import User
from django.db import models
from django.utils import timezone


def _default(name, fallback):
    return Decimal(str(getattr(settings, name, fallback)))


class AiSpend(models.Model):
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='ai_spend',
                             db_index=False)  # db_review §4
    visitor = models.CharField(max_length=64, blank=True, db_index=True)  # before an account: session + IP hash
    event = models.ForeignKey('events.Event', on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
                              db_index=False)  # db_review §4
    feature = models.CharField(max_length=32, db_index=True)
    provider = models.CharField(max_length=16, blank=True)
    model = models.CharField(max_length=64, blank=True)
    input_tokens = models.PositiveIntegerField(default=0)
    output_tokens = models.PositiveIntegerField(default=0)
    cost_usd = models.DecimalField(max_digits=10, decimal_places=5, default=0)
    request_id = models.CharField(max_length=80, blank=True)
    counted = models.BooleanField(default=True)  # against the person's monthly limit
    background = models.BooleanField(default=False)  # started by Attnd for the event (photos, suggestions, watching…)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        indexes = [models.Index(fields=['user', 'created_at']), models.Index(fields=['event', 'created_at'])]

    def __str__(self):
        return f'{self.feature} ${self.cost_usd} ({self.user_id or self.visitor or "attnd"})'


class AiLimits(models.Model):
    """The live AI limits (pk 1). Loosening one always asks (Ask Attnd confirm); History logs every change."""
    monthly_default_usd = models.DecimalField('Monthly AI credit per person (USD)', max_digits=8, decimal_places=2,
                                              default=30)
    visitor_allowance_usd = models.DecimalField('Visitors before an account (USD)', max_digits=8, decimal_places=2,
                                                default=5)
    platform_alert_usd = models.DecimalField('Email the owner when the platform passes (USD a month)', max_digits=9,
                                             decimal_places=2, default=100)
    agent_daily_usd = models.DecimalField('Ask Attnd per person per day (USD)', max_digits=8, decimal_places=2, default=2)
    studio_daily_usd = models.DecimalField('Studio per event per day (USD)', max_digits=8, decimal_places=2, default=10)
    place_images_daily_usd = models.DecimalField('Place photos, all events, per day (USD)', max_digits=8,
                                                 decimal_places=2, default=1)
    covers_daily_usd = models.DecimalField('Home page covers per day (USD)', max_digits=8, decimal_places=2, default=2)
    prospects_daily_usd = models.DecimalField('Prospects per day (USD)', max_digits=8, decimal_places=2, default=5)
    new_account_daily_imports = models.PositiveIntegerField('AI imports a day for accounts under a week old', default=3)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')

    class Meta:
        verbose_name = verbose_name_plural = 'AI limits'

    def __str__(self):
        return 'AI limits'

    # Field → the environment variable that gives its first default.
    ENV = {'monthly_default_usd': ('AI_MONTHLY_CREDIT_USD', 30), 'visitor_allowance_usd': ('AI_VISITOR_CREDIT_USD', 5),
           'platform_alert_usd': ('AI_PLATFORM_ALERT_USD', 100), 'agent_daily_usd': ('AGENT_DAILY_SPEND_CAP_USD', 2),
           'studio_daily_usd': ('STUDIO_DAILY_SPEND_CAP_USD', 10),
           'place_images_daily_usd': ('PLACE_IMAGES_DAILY_CAP_USD', 1),
           'covers_daily_usd': ('EVENT_COVERS_DAILY_CAP_USD', 2), 'prospects_daily_usd': ('PROSPECT_DAILY_SPEND_CAP_USD', 5),
           'new_account_daily_imports': ('NEW_ACCOUNT_DAILY_IMPORTS', 3)}

    @classmethod
    def load(cls):
        from django.core.cache import caches
        values = {}
        for field, (env, fallback) in cls.ENV.items():
            raw = getattr(settings, env, fallback)
            values[field] = int(raw) if field == 'new_account_daily_imports' else Decimal(str(raw))
        if getattr(settings, 'TESTING', False):
            # Tests: never cached; without a saved row the settings (override_settings) decide.
            return cls.objects.filter(pk=1).first() or cls(pk=1, **values)
        cache = caches['local']
        found = cache.get('ai_limits')
        if found is None:
            found, _ = cls.objects.get_or_create(pk=1, defaults=values)
            cache.set('ai_limits', found, 30)
        return found

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        from django.core.cache import caches
        caches['local'].delete('ai_limits')


class AiLimitOverride(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='ai_limit')
    monthly_usd = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    unlimited = models.BooleanField(default=False)
    note = models.CharField(max_length=200, blank=True)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')

    def __str__(self):
        return f'{self.user.email}: {"no limit" if self.unlimited else self.monthly_usd}'


class AiCreditNotice(models.Model):
    """A warning sent once per month: a person at 80 % / 100 %, or the platform alert (user empty)."""
    user = models.ForeignKey(User, on_delete=models.CASCADE, null=True, blank=True, related_name='+', db_index=False)
    month = models.CharField(max_length=7)  # YYYY-MM
    level = models.CharField(max_length=16)  # 80 / 100 / platform
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['user', 'month', 'level'], name='credit_notice_once',
                                               nulls_distinct=False)]
