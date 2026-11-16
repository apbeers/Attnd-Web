"""Attnd Studio: AI-designed event websites hosted by Attnd (docs/specs/studio_spec.md)."""

import re
import secrets
import uuid

from django.contrib.auth.models import User
from django.db import models
from django.utils import timezone as dj_tz
from django.utils.text import slugify

from events.models import Event


class StudioAccess(models.Model):
    """An email invited to the Studio beta. Matched case-insensitively against the account's email, so it works before
    the person has an account (like EventInvite)."""
    email = models.EmailField(unique=True)
    invited_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    created_at = models.DateTimeField(default=dj_tz.now)

    class Meta:
        ordering = ['email']
        verbose_name_plural = 'Studio access'

    def save(self, *args, **kwargs):
        self.email = (self.email or '').strip().lower()
        super().save(*args, **kwargs)

    def __str__(self):
        return self.email


SLUG_RE = re.compile(r'^[a-z0-9](?:[a-z0-9-]{1,48})[a-z0-9]$')
RESERVED_SLUGS = {'_attnd', 'admin', 'api', 'console', 'static', 'media', 'accounts', 'robots', 'robots-txt',
                  'favicon', 'well-known', 'health', 'join', 'attnd', 'studio', 'www', 'report', 'preview', 'p',
                  'sitemap', 'login', 'csp-report'}


def new_secret():
    return secrets.token_hex(16)


def unique_slug(name, exclude_pk=None):
    base = slugify(name or '')[:40].strip('-') or 'event'
    if len(base) < 3:
        base = f'{base}-event'
    candidate, n = base, 2
    while (candidate in RESERVED_SLUGS
           or Site.objects.filter(slug=candidate).exclude(pk=exclude_pk).exists()):
        candidate = f'{base}-{n}'
        n += 1
    return candidate


class Site(models.Model):
    """The event's website. `draft` is what the editor shows; `published` (null = not live) is what the public sees."""
    event = models.OneToOneField(Event, on_delete=models.CASCADE, related_name='studio_site')
    slug = models.CharField(max_length=50, unique=True)
    draft = models.ForeignKey('SiteVersion', on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    published = models.ForeignKey('SiteVersion', on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    published_at = models.DateTimeField(null=True, blank=True)
    published_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    suspended = models.BooleanField(default=False, help_text='Taken down by staff (reports, abuse)')
    suspended_reason = models.CharField(max_length=256, blank=True)
    preview_secret = models.CharField(max_length=32, default=new_secret)
    applied_flyer = models.CharField(max_length=40, blank=True)  # asset id last copied into Event.flyer
    notify_on_forms = models.BooleanField(default=True, help_text='Email the owner and admins on each form entry')
    created_at = models.DateTimeField(default=dj_tz.now)

    def __str__(self):
        return f'{self.slug} ({self.event})'

    @property
    def is_live(self):
        return bool(self.published_id) and not self.suspended and self.event.plan == Event.PLAN_STUDIO

    def public_url(self):
        from studio import hosting
        return hosting.site_url(self)

    def next_number(self):
        last = self.versions.order_by('-number').values_list('number', flat=True).first()
        return (last or 0) + 1


class SiteVersion(models.Model):
    SOURCE_START, SOURCE_AI, SOURCE_CODE, SOURCE_RESTORE, SOURCE_IMPORT = 'start', 'ai', 'code', 'restore', 'import'
    SOURCE_CHOICES = [(SOURCE_START, 'Started'), (SOURCE_AI, 'AI'), (SOURCE_CODE, 'Code edit'),
                      (SOURCE_RESTORE, 'Restored'), (SOURCE_IMPORT, 'Imported')]

    site = models.ForeignKey(Site, on_delete=models.CASCADE, related_name='versions')
    number = models.PositiveIntegerField()
    files = models.JSONField(default=dict, blank=True)       # {"index.html": "<!doctype html>…", "styles.css": …}
    app_look = models.JSONField(default=dict, blank=True)    # {"accent_color", "logo_url", "display": {…}}
    flyer_asset = models.ForeignKey('StudioAsset', on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    summary = models.CharField(max_length=256, blank=True)
    source = models.CharField(max_length=16, choices=SOURCE_CHOICES, default=SOURCE_AI)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    run = models.ForeignKey('StudioRun', on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    created_at = models.DateTimeField(default=dj_tz.now)

    class Meta:
        ordering = ['-number']
        constraints = [models.UniqueConstraint(fields=['site', 'number'], name='site_version_number')]

    def __str__(self):
        return f'{self.site.slug} v{self.number}'

    @property
    def pages(self):
        return sorted(p for p in self.files if p.endswith('.html'))


def asset_storage():
    """Assets are any file type (fonts, PDFs, SVGs, images): Cloudinary's raw storage when Cloudinary is configured
    (its image storage refuses fonts), else the default storage (the media folder)."""
    import os

    from django.conf import settings
    from django.core.files.storage import default_storage
    if os.getenv('CLOUDINARY_URL') and 'Cloudinary' in settings.STORAGES['default']['BACKEND']:
        from cloudinary_storage.storage import RawMediaCloudinaryStorage
        return RawMediaCloudinaryStorage()
    return default_storage


def asset_path(instance, filename):
    return f'studio/{instance.event_id}/{instance.id.hex[:12]}-{filename}'


class StudioAsset(models.Model):
    KIND_IMAGE, KIND_SVG, KIND_PDF, KIND_FONT = 'image', 'svg', 'pdf', 'font'
    KIND_CHOICES = [(KIND_IMAGE, 'Image'), (KIND_SVG, 'SVG'), (KIND_PDF, 'PDF'), (KIND_FONT, 'Font')]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name='studio_assets')
    file = models.FileField(upload_to=asset_path, storage=asset_storage, max_length=512)
    name = models.CharField(max_length=128)  # safe file name used in URLs
    kind = models.CharField(max_length=8, choices=KIND_CHOICES)
    content_type = models.CharField(max_length=64)
    size = models.PositiveIntegerField(default=0)
    width = models.PositiveIntegerField(null=True, blank=True)
    height = models.PositiveIntegerField(null=True, blank=True)
    description = models.CharField(max_length=1000, blank=True)
    alt = models.CharField(max_length=300, blank=True)
    tags = models.JSONField(default=list, blank=True)
    colors = models.JSONField(default=list, blank=True)
    generated = models.BooleanField(default=False)  # made by the image model
    origin_url = models.URLField(max_length=1024, blank=True)  # copied from the organizer's old website
    sha256 = models.CharField(max_length=64, blank=True)  # of the bytes: imports reuse an identical file
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    created_at = models.DateTimeField(default=dj_tz.now)

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['event', 'sha256'], name='studio_asset_event_sha')]

    def __str__(self):
        return self.name

    @property
    def site_path(self):
        """Relative to the site's base: what pages reference."""
        return f'_attnd/assets/{self.id}/{self.name}'


class StudioRun(models.Model):
    """One AI turn in the editor (a chat request, or bringing a website in). Runs in a background thread; the
    working copy is saved after every tool call so the preview shows the changes as they land."""
    KIND_CHAT, KIND_IMPORT = 'chat', 'import'
    QUEUED, RUNNING, DONE, FAILED = 'queued', 'running', 'done', 'failed'
    STATUS_CHOICES = [(QUEUED, 'Queued'), (RUNNING, 'Running'), (DONE, 'Done'), (FAILED, 'Failed')]

    site = models.ForeignKey(Site, on_delete=models.CASCADE, related_name='runs')
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    kind = models.CharField(max_length=8, default=KIND_CHAT)
    prompt = models.TextField()
    selection = models.JSONField(default=dict, blank=True)
    context = models.JSONField(default=dict, blank=True)  # import: the captured look of the old website
    status = models.CharField(max_length=8, choices=STATUS_CHOICES, default=QUEUED, db_index=True)
    steps = models.JSONField(default=list, blank=True)
    working_files = models.JSONField(null=True, blank=True)
    working_look = models.JSONField(null=True, blank=True)
    working_flyer = models.CharField(max_length=40, blank=True)
    files_rev = models.PositiveIntegerField(default=0)
    base_version = models.ForeignKey(SiteVersion, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    result_version = models.ForeignKey(SiteVersion, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    reply = models.TextField(blank=True)
    error = models.TextField(blank=True)
    provider = models.CharField(max_length=16, blank=True)
    model = models.CharField(max_length=64, blank=True)
    input_tokens = models.PositiveIntegerField(default=0)
    output_tokens = models.PositiveIntegerField(default=0)
    cost_usd = models.DecimalField(max_digits=9, decimal_places=4, default=0)
    created_at = models.DateTimeField(default=dj_tz.now)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']

    @property
    def is_active(self):
        return self.status in (self.QUEUED, self.RUNNING)


class StudioMessage(models.Model):
    ROLE_USER, ROLE_ASSISTANT = 'user', 'assistant'
    site = models.ForeignKey(Site, on_delete=models.CASCADE, related_name='messages')
    role = models.CharField(max_length=12)
    text = models.TextField()
    selection = models.JSONField(default=dict, blank=True)
    run = models.ForeignKey(StudioRun, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    created_at = models.DateTimeField(default=dj_tz.now)

    class Meta:
        ordering = ['created_at', 'id']


class StudioUsage(models.Model):
    """What every Studio AI call cost (the spend cap sums it per event per UTC day)."""
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name='studio_usage')
    run = models.ForeignKey(StudioRun, on_delete=models.SET_NULL, null=True, blank=True, related_name='usage')
    kind = models.CharField(max_length=12)  # chat, image, describe
    provider = models.CharField(max_length=16)
    model = models.CharField(max_length=64)
    input_tokens = models.PositiveIntegerField(default=0)
    output_tokens = models.PositiveIntegerField(default=0)
    images = models.PositiveIntegerField(default=0)
    cost_usd = models.DecimalField(max_digits=9, decimal_places=4, default=0)
    created_at = models.DateTimeField(default=dj_tz.now, db_index=True)

    class Meta:
        ordering = ['-created_at']


class FormSubmission(models.Model):
    """An entry from a hosted site's form (newsletter, applications, contact). No IP address or user agent kept."""
    site = models.ForeignKey(Site, on_delete=models.CASCADE, related_name='submissions')
    form = models.CharField(max_length=40)
    data = models.JSONField(default=dict)
    page = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(default=dj_tz.now)

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['site', 'form', 'created_at'])]


class SiteReport(models.Model):
    REASONS = [('spam', 'Spam'), ('scam', 'Scam or fraud'), ('malware', 'Malware or phishing'),
               ('impersonation', 'Pretends to be someone else'), ('offensive', 'Hateful or offensive'),
               ('other', 'Something else')]
    OPEN, RESOLVED = 'open', 'resolved'
    site = models.ForeignKey(Site, on_delete=models.CASCADE, related_name='reports')
    reason = models.CharField(max_length=16, choices=REASONS)
    details = models.TextField(blank=True, max_length=2000)
    email = models.EmailField(blank=True)
    page = models.CharField(max_length=200, blank=True)
    status = models.CharField(max_length=10, default=OPEN)
    created_at = models.DateTimeField(default=dj_tz.now)

    class Meta:
        ordering = ['-created_at']
