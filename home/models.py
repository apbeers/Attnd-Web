"""The public home page's own records (Nov 21, 2026 onboarding):

- ``PendingImport``: what a visitor typed or attached in the home page's composer before they had an account. Kept in
  the database (bytes like ``IngestJob.upload_data``), referenced from their session; after sign-up / sign-in
  ``home.onboarding.consume`` turns it into their AI import (a demo event they own). Unused ones are purged after a day.
- ``HomeMetric``: anonymous counts of the page's own interactions (chip taps, QR views, composer submits) per day; no
  identifier, no text, no IP.
"""

import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone as dj_tz


class PendingImport(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    text = models.TextField(blank=True)
    urls = models.JSONField(default=list, blank=True)
    upload_name = models.CharField(max_length=256, blank=True)
    upload_type = models.CharField(max_length=128, blank=True)
    upload_data = models.BinaryField(null=True, blank=True)
    time_zone = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(default=dj_tz.now, db_index=True)
    used_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    used_at = models.DateTimeField(null=True, blank=True)
    job_id = models.UUIDField(null=True, blank=True)

    def __str__(self):
        return f'{self.created_at:%Y-%m-%d %H:%M} {self.upload_name or self.text[:40]}'


class HomeMetric(models.Model):
    NAMES = {
        'chip': 'Composer chip tapped',
        'submit': 'Composer submitted',
        'qr_view': 'Gallery QR shown',
        'open_app': 'Gallery Open in Attnd tapped',
        'claim': 'Claim it tapped',
        'gallery_filter': 'Gallery category chosen',
    }
    day = models.DateField()
    name = models.CharField(max_length=32)
    detail = models.CharField(max_length=64, blank=True)
    count = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['day', 'name', 'detail'], name='home_metric_once')]

    def __str__(self):
        return f'{self.day} {self.name} {self.detail}: {self.count}'

    @classmethod
    def add(cls, name, detail=''):
        from django.db.models import F
        if name not in cls.NAMES:
            return False
        detail = ''.join(ch for ch in str(detail or '').lower() if ch.isalnum() or ch in '-_')[:64]
        day = dj_tz.now().date()
        row, created = cls.objects.get_or_create(day=day, name=name, detail=detail, defaults={'count': 1})
        if not created:
            cls.objects.filter(pk=row.pk).update(count=F('count') + 1)
        return True
