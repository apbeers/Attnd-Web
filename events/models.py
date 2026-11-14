import secrets
import string
import uuid
from decimal import Decimal

from django.contrib.auth.models import User
from django.db import models
from django.urls import reverse
from django.utils import timezone as dj_tz


def get_file_path(instance, filename):
    file, ext = filename.split('.')
    simple_uuid = str(uuid.uuid4())[:5]
    return f'{file}_{simple_uuid}.{ext}'


def normalize_coord(value):
    """Store coordinates at 7 decimal places (~1 cm) so float noise never looks like an edit."""
    if value is None or value == '':
        return None
    return Decimal(str(round(float(value), 7)))


class CoordinatesMixin:
    def save(self, *args, **kwargs):
        self.latitude = normalize_coord(self.latitude)
        self.longitude = normalize_coord(self.longitude)
        super().save(*args, **kwargs)


def generate_event_code(length: int = 8) -> str:
    """Return a random uppercase alphanumeric code that doesn't already exist."""
    alphabet = string.ascii_uppercase + string.digits
    for _ in range(20):
        code = ''.join(secrets.choice(alphabet) for _ in range(length))
        if not Event.objects.filter(event_code=code).exists():
            return code
    raise RuntimeError('Could not generate a unique event code after 20 attempts.')


class Event(CoordinatesMixin, models.Model):
    STATUS_DRAFT = 'draft'
    STATUS_PUBLISHED = 'published'
    STATUS_ARCHIVED = 'archived'
    STATUS_CHOICES = [
        (STATUS_DRAFT, 'Draft'),
        (STATUS_PUBLISHED, 'Published'),
        (STATUS_ARCHIVED, 'Archived'),
    ]

    REFRESH_OFF = 'off'
    REFRESH_DAILY = 'daily'
    REFRESH_EVENT_WEEK = 'event_week'
    REFRESH_CHOICES = [
        (REFRESH_OFF, 'Off — only when I ask'),
        (REFRESH_DAILY, 'Daily'),
        (REFRESH_EVENT_WEEK, 'Daily, then hourly on event days'),
    ]

    name = models.CharField(max_length=256)
    event_code = models.CharField(max_length=16, unique=True)
    description = models.CharField(max_length=2048, blank=True)
    latitude = models.DecimalField(max_digits=24, decimal_places=20, null=True, blank=True)
    longitude = models.DecimalField(max_digits=24, decimal_places=20, null=True, blank=True)
    start_time = models.DateTimeField()
    end_time = models.DateTimeField(null=True, blank=True)
    timezone = models.CharField(max_length=64, default='UTC',
                                help_text='IANA time zone, e.g. America/Chicago')
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_PUBLISHED)
    paid = models.BooleanField(default=False)
    public = models.BooleanField(default=False, help_text='Listed in nearby search')
    flyer = models.FileField(upload_to=get_file_path, blank=True)
    address = models.CharField(max_length=512, blank=True)
    order_id = models.CharField(max_length=512, blank=True)
    created_at = models.DateTimeField(default=dj_tz.now, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE)

    # Presentation — organizer overrides only; resolved by events.display
    display_config = models.JSONField(default=dict, blank=True)
    accent_color = models.CharField(max_length=7, blank=True, help_text='#RRGGBB')
    logo_url = models.URLField(max_length=1024, blank=True)

    # Sync — bumped on every content change so clients can cheaply detect updates
    content_version = models.PositiveIntegerField(default=1)
    content_updated_at = models.DateTimeField(default=dj_tz.now)

    # AI sources & refresh
    source_urls = models.JSONField(default=list, blank=True)
    source_text = models.TextField(blank=True)
    refresh_schedule = models.CharField(max_length=16, choices=REFRESH_CHOICES, default=REFRESH_OFF)
    auto_accept_minor = models.BooleanField(
        default=False, help_text='Automatically apply small AI edits (typos, descriptions, images)')
    last_refreshed_at = models.DateTimeField(null=True, blank=True)
    locked_fields = models.JSONField(default=list, blank=True,
                                     help_text='Fields edited by a person; AI refreshes will not overwrite them')

    # Fields the AI / diff engine manages
    CONTENT_FIELDS = ['name', 'description', 'address', 'latitude', 'longitude',
                      'start_time', 'end_time', 'timezone']

    @property
    def is_live(self):
        return self.status != self.STATUS_DRAFT

    def bump_version(self, save=True):
        Event.objects.filter(pk=self.pk).update(
            content_version=models.F('content_version') + 1,
            content_updated_at=dj_tz.now(),
        )
        self.refresh_from_db(fields=['content_version', 'content_updated_at'])

    def can_edit(self, user):
        if not user.is_authenticated:
            return False
        if user.is_superuser or self.user_id == user.id:
            return True
        return self.members.filter(user=user).exists()

    class Meta:
        indexes = [
            models.Index(fields=['user', 'name', 'event_code', 'latitude', 'longitude'])
        ]

    def get_absolute_url(self):
        return reverse('events:get_single_event', args=[self.id])

    def __str__(self):
        return self.name


class Location(CoordinatesMixin, models.Model):
    name = models.CharField(max_length=256)
    snippet = models.CharField(max_length=200, blank=True)
    description = models.CharField(max_length=1024, blank=True)
    website = models.URLField(max_length=1024, null=True, blank=True)
    image = models.ImageField(upload_to=get_file_path, blank=True, null=True)
    image_url = models.URLField(max_length=1024, blank=True, null=True)
    latitude = models.DecimalField(max_digits=24, decimal_places=20, null=True, blank=True)
    longitude = models.DecimalField(max_digits=24, decimal_places=20, null=True, blank=True)
    address = models.CharField(max_length=512, blank=True)
    category = models.CharField(max_length=64, blank=True,
                                help_text='e.g. Stage, Food, Vendor, Restroom')
    sort_order = models.IntegerField(default=0)
    hidden = models.BooleanField(default=False)
    locked_fields = models.JSONField(default=list, blank=True)
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    event = models.ForeignKey(Event, on_delete=models.CASCADE)

    CONTENT_FIELDS = ['name', 'snippet', 'description', 'website', 'image_url',
                      'address', 'latitude', 'longitude', 'category']

    class Meta:
        indexes = [
            models.Index(fields=['event', 'user'])
        ]
        ordering = ['sort_order', 'name']

    @property
    def has_coordinates(self):
        return self.latitude is not None and self.longitude is not None

    @property
    def resolved_image_url(self):
        if self.image:
            return self.image.url
        return self.image_url or None

    def get_absolute_url(self):
        return reverse('events:get_single_location', args=[self.event.id, self.id])

    def __str__(self):
        return self.name


class ScheduledItem(models.Model):
    name = models.CharField(max_length=256)
    description = models.CharField(max_length=1024, blank=True)
    start_time = models.DateTimeField()
    end_time = models.DateTimeField(null=True, blank=True)
    location = models.ForeignKey(Location, on_delete=models.SET_NULL, null=True, blank=True)
    tags = models.JSONField(default=list, blank=True)
    hidden = models.BooleanField(default=False)
    locked_fields = models.JSONField(default=list, blank=True)
    event = models.ForeignKey(Event, on_delete=models.CASCADE)
    user = models.ForeignKey(User, on_delete=models.CASCADE)

    CONTENT_FIELDS = ['name', 'description', 'start_time', 'end_time', 'location', 'tags']

    class Meta:
        indexes = [
            models.Index(fields=['event', 'start_time']),
            models.Index(fields=['location', 'start_time']),
        ]
        ordering = ['start_time']

    def __str__(self):
        return f'{self.name} @ {self.location.name if self.location else "—"}'


class Notification(models.Model):
    title = models.CharField(max_length=60)
    body = models.CharField(max_length=256)
    send_time = models.DateTimeField()
    sent = models.BooleanField(default=False)
    sent_at = models.DateTimeField(null=True, blank=True)
    delivered_count = models.PositiveIntegerField(default=0)
    failed_count = models.PositiveIntegerField(default=0)
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    event = models.ForeignKey(Event, on_delete=models.CASCADE)

    class Meta:
        indexes = [
            models.Index(fields=['event', 'user'])
        ]

    def get_absolute_url(self):
        return reverse('events:get_single_notification', args=[self.event.id, self.id])

    def __str__(self):
        return self.title


class Device(models.Model):
    id = models.CharField(max_length=128, primary_key=True)
    event = models.ForeignKey(Event, on_delete=models.CASCADE, null=True, blank=True)
    subscribed = models.BooleanField(default=True)
    platform = models.CharField(max_length=12)
    push_token = models.CharField(max_length=512, blank=True)
    app_version = models.CharField(max_length=32, blank=True)
    last_seen = models.DateTimeField(null=True, blank=True)
    joined_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=['event'])
        ]

    def __str__(self):
        return f'{self.event} - {self.id} - {self.subscribed}'


class EventMember(models.Model):
    """Additional organizers who can manage an event (the owner is Event.user)."""
    ROLE_EDITOR = 'editor'
    ROLE_CHOICES = [(ROLE_EDITOR, 'Editor')]

    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name='members')
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='event_memberships')
    role = models.CharField(max_length=16, choices=ROLE_CHOICES, default=ROLE_EDITOR)
    created_at = models.DateTimeField(default=dj_tz.now)

    class Meta:
        unique_together = [('event', 'user')]

    def __str__(self):
        return f'{self.user} → {self.event}'


class EventRevision(models.Model):
    """A full snapshot of an event's content, taken before every bulk change, for rollback."""
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name='revisions')
    content_version = models.PositiveIntegerField()
    snapshot = models.JSONField()
    summary = models.CharField(max_length=256, blank=True)
    created_at = models.DateTimeField(default=dj_tz.now)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.event} v{self.content_version}: {self.summary}'


def events_for_user(user):
    """Events the user owns or is a member of. Superusers see everything."""
    if not user.is_authenticated:
        return Event.objects.none()
    if user.is_superuser:
        return Event.objects.all()
    return Event.objects.filter(models.Q(user=user) | models.Q(members__user=user)).distinct()
