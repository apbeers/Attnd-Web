import uuid

from django.contrib.auth.models import User
from django.db import models
from django.utils import timezone as dj_tz

from events.models import Event


class IngestJob(models.Model):
    """One AI run: import a new event, or refresh an existing one from its sources."""

    KIND_CREATE = 'create'
    KIND_REFRESH = 'refresh'
    KIND_CHOICES = [(KIND_CREATE, 'Create event'), (KIND_REFRESH, 'Refresh event')]

    QUEUED, RUNNING, DONE, FAILED = 'queued', 'running', 'done', 'failed'
    STATUS_CHOICES = [(QUEUED, 'Queued'), (RUNNING, 'Running'), (DONE, 'Done'), (FAILED, 'Failed')]

    TRIGGER_TRIAL, TRIGGER_CONSOLE, TRIGGER_SCHEDULE = 'trial', 'console', 'schedule'
    # The website watcher saw pages change: re-read only those pages (job.urls), without crawling.
    TRIGGER_WATCH = 'watch'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    kind = models.CharField(max_length=16, choices=KIND_CHOICES)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=QUEUED, db_index=True)
    trigger = models.CharField(max_length=16, default=TRIGGER_CONSOLE)
    event = models.ForeignKey(Event, on_delete=models.CASCADE, null=True, blank=True, related_name='ingest_jobs')
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)

    # Inputs
    urls = models.JSONField(default=list, blank=True)
    text = models.TextField(blank=True)
    instructions = models.TextField(blank=True, help_text='Extra guidance for the AI')
    upload_name = models.CharField(max_length=256, blank=True)
    upload_type = models.CharField(max_length=128, blank=True)
    upload_data = models.BinaryField(null=True, blank=True)
    timezone = models.CharField(max_length=64, blank=True)
    # Attnd Studio: when the import finishes, also bring the first source website into Studio (studio.importer).
    studio_import = models.BooleanField(default=False)

    # Progress / output
    progress = models.CharField(max_length=256, blank=True)
    error = models.TextField(blank=True)
    result = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(default=dj_tz.now)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.get_kind_display()} [{self.status}] {self.event or self.urls}'

    @property
    def is_finished(self):
        return self.status in (self.DONE, self.FAILED)


class ChangeSet(models.Model):
    """Proposed edits to an event, produced by an AI refresh, awaiting organizer review."""

    PENDING, APPLIED, DISCARDED, EMPTY = 'pending', 'applied', 'discarded', 'empty'
    STATUS_CHOICES = [(PENDING, 'Needs review'), (APPLIED, 'Applied'),
                      (DISCARDED, 'Discarded'), (EMPTY, 'No changes')]

    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name='changesets')
    job = models.OneToOneField(IngestJob, on_delete=models.SET_NULL, null=True, blank=True,
                               related_name='changeset')
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=PENDING, db_index=True)
    summary = models.TextField(blank=True)
    notes = models.TextField(blank=True, help_text="The AI's notes about uncertain information")
    base_version = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(default=dj_tz.now)
    applied_at = models.DateTimeField(null=True, blank=True)
    applied_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'Changes for {self.event} ({self.status})'


class Change(models.Model):
    ENTITY_EVENT, ENTITY_LOCATION, ENTITY_ITEM = 'event', 'location', 'item'
    ENTITY_CHOICES = [(ENTITY_EVENT, 'Event'), (ENTITY_LOCATION, 'Location'), (ENTITY_ITEM, 'Schedule item')]
    OP_CREATE, OP_UPDATE, OP_DELETE = 'create', 'update', 'delete'
    OP_CHOICES = [(OP_CREATE, 'Add'), (OP_UPDATE, 'Update'), (OP_DELETE, 'Remove')]
    PENDING, ACCEPTED, REJECTED = 'pending', 'accepted', 'rejected'
    STATUS_CHOICES = [(PENDING, 'Pending'), (ACCEPTED, 'Accepted'), (REJECTED, 'Rejected')]

    changeset = models.ForeignKey(ChangeSet, on_delete=models.CASCADE, related_name='changes')
    entity = models.CharField(max_length=16, choices=ENTITY_CHOICES)
    op = models.CharField(max_length=16, choices=OP_CHOICES)
    target_id = models.BigIntegerField(null=True, blank=True)
    ref = models.CharField(max_length=32, blank=True, help_text='Temporary key for created rows')
    label = models.CharField(max_length=256, blank=True)
    # create: {field: value}; update: {field: {"old": x, "new": y}}; delete: {}
    data = models.JSONField(default=dict)
    conflict_fields = models.JSONField(default=list, blank=True)
    reason = models.TextField(blank=True)
    minor = models.BooleanField(default=False)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=PENDING)
    accepted_fields = models.JSONField(null=True, blank=True)
    sort = models.IntegerField(default=0)

    class Meta:
        ordering = ['sort', 'id']

    def __str__(self):
        return f'{self.get_op_display()} {self.entity} {self.label}'


class SourcePage(models.Model):
    """One web page an event's content comes from, fingerprinted so the watcher can tell cheaply
    whether it changed (scrape.watch). AI runs only when a fingerprint changes."""
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name='source_pages')
    url = models.URLField(max_length=1024)
    etag = models.CharField(max_length=256, blank=True)
    last_modified = models.CharField(max_length=64, blank=True)
    content_hash = models.CharField(max_length=64, blank=True)  # what the event currently reflects
    pending_hash = models.CharField(max_length=64, blank=True)  # a change not yet read by the AI
    checked_at = models.DateTimeField(null=True, blank=True)
    changed_at = models.DateTimeField(null=True, blank=True)
    last_status = models.CharField(max_length=32, blank=True)  # e.g. "200", "304", "error: …"
    failures = models.PositiveSmallIntegerField(default=0)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['event', 'url'], name='source_page_unique')]
        ordering = ['id']

    @property
    def has_pending_change(self):
        return bool(self.pending_hash) and self.pending_hash != self.content_hash

    def __str__(self):
        return self.url
