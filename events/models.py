import secrets
import string
import uuid
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.db import models
from django.db.models.functions import Upper
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


# Tags (API-010): short lowercase labels on locations and schedule items ("live music", "bbq", "kid-friendly").
MAX_TAGS = 8
MAX_TAG_LENGTH = 32


def normalize_tags(values):
    """Lower-case, trimmed, single-spaced, de-duplicated tags (order kept), at most MAX_TAGS of MAX_TAG_LENGTH.

    Accepts a list or a comma-separated string. Anything that isn't text is dropped, so a bad value never
    breaks a save or the bundle."""
    if isinstance(values, str):
        values = values.split(',')
    if not isinstance(values, (list, tuple)):
        return []
    out = []
    for value in values:
        if not isinstance(value, str):
            continue
        tag = ' '.join(value.replace('#', ' ').split()).lower().strip(' .,;:')[:MAX_TAG_LENGTH].strip()
        if tag and tag not in out:
            out.append(tag)
    return out[:MAX_TAGS]


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
    REFRESH_WATCH = 'watch'
    REFRESH_CHOICES = [
        (REFRESH_OFF, 'Off: only when I ask'),
        (REFRESH_WATCH, 'Watch my website for changes'),
    ]

    UPDATE_REVIEW = 'review'
    UPDATE_MINOR = 'minor'
    UPDATE_AUTO = 'auto'
    UPDATE_MODE_CHOICES = [
        (UPDATE_REVIEW, 'Ask me to review every change'),
        (UPDATE_MINOR, 'Apply small fixes, ask about the rest'),
        (UPDATE_AUTO, 'Apply changes automatically'),
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
    # Tier (no checkout yet): standard = import/manage the event; studio = Attnd Studio (AI designs the site, flyer
    # and app look; Attnd hosts the site). See studio/ and docs/specs/studio_spec.md.
    PLAN_STANDARD = 'standard'
    PLAN_STUDIO = 'studio'
    PLAN_CHOICES = [(PLAN_STANDARD, 'Standard'), (PLAN_STUDIO, 'Studio')]
    plan = models.CharField(max_length=16, choices=PLAN_CHOICES, default=PLAN_STANDARD)
    public = models.BooleanField(default=False, help_text='Listed in nearby search')
    # Staff test copies of prospects' events (prospects/): published (joinable by code / QR) but never listed in
    # nearby search, never website-watched, never brought into Studio automatically. Not part of the API.
    testing_only = models.BooleanField(default=False, help_text='Staff test copy: never listed or watched')
    # Showcase events (manage.py load_showcase_events, dev only for now): the home page's "See it live" gallery. Kept
    # happening now like DEMO; exempt from the free limit (API-025), never in nearby search, no usage counts.
    showcase = models.BooleanField(default=False, help_text='Home page showcase event (gallery, kept happening now)')
    # The home page gallery's card cover (events/covers.py), only for events with no flyer or place photo of their own:
    # a licensed Wikimedia Commons photo near the venue (credit shown on the card) or an AI-made picture. Never copied
    # from the organizer's website. Not part of the API.
    cover = models.FileField(upload_to=get_file_path, blank=True)
    cover_credit = models.CharField(max_length=300, blank=True)
    cover_source = models.CharField(max_length=16, blank=True)  # wikimedia / ai
    cover_checked_at = models.DateTimeField(null=True, blank=True)
    cover_cost_usd = models.DecimalField(max_digits=8, decimal_places=4, default=0)
    # API-021: "Plan your day" examples written for this event (events/plan_suggestions.py): {items: [{text, kind}],
    # version: the content_version they were made for, source: ai | fallback, at, cost_usd}. Kept up to date in the
    # background after content changes; the bundle falls back to the deterministic ones when empty or stale.
    plan_suggestions = models.JSONField(default=dict, blank=True)
    flyer = models.FileField(upload_to=get_file_path, blank=True)
    address = models.CharField(max_length=512, blank=True)
    order_id = models.CharField(max_length=512, blank=True)
    created_at = models.DateTimeField(default=dj_tz.now, editable=False)
    # The owner. Null = unassigned (the owner's account was deleted); site admins can reassign it.
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)

    # Presentation — organizer overrides only; resolved by events.display
    display_config = models.JSONField(default=dict, blank=True)
    # API-024: the organizer's screen layouts (events/layout.py `clean_config`: {"screens": {screen: {sections,
    # overrides?}}}); screens not listed use the automatic layout.
    layout_config = models.JSONField(default=dict, blank=True)
    accent_color = models.CharField(max_length=7, blank=True, help_text='#RRGGBB')
    logo_url = models.URLField(max_length=1024, blank=True)
    # Our own copy of an outside logo_url (attnd/images.py: self-hosted, re-encoded); logo_source_url = the logo_url it
    # was made from, so a changed logo_url is copied again. The API and pages show the copy (images.event_logo_url).
    logo_image = models.FileField(upload_to=get_file_path, blank=True)
    logo_source_url = models.URLField(max_length=1024, blank=True)
    # API-023: the event's colour theme (events/theme.py): {light: {primary, secondary, tertiary}, dark: {…}, source:
    # ai | site | manual | default, made_at, reason}. {} = Attnd's purple. Made by the AI import (events/theme_ai.py),
    # edited in the console (an edit locks it); theme_web / theme_apps switch it on for the website and the apps.
    theme = models.JSONField(default=dict, blank=True)
    theme_web = models.BooleanField(default=True, db_default=True, help_text='Use the custom theme on the website')
    theme_apps = models.BooleanField(default=True, db_default=True, help_text='Use the custom theme in the apps')

    # Sync — bumped on every content change so clients can cheaply detect updates
    content_version = models.PositiveIntegerField(default=1, db_default=1)
    content_updated_at = models.DateTimeField(default=dj_tz.now)

    # AI sources & refresh
    source_urls = models.JSONField(default=list, blank=True)
    source_text = models.TextField(blank=True)
    # New events stay in sync with their website by default (organizers can turn it off).
    refresh_schedule = models.CharField(max_length=16, choices=REFRESH_CHOICES, default=REFRESH_WATCH)
    update_mode = models.CharField(max_length=8, choices=UPDATE_MODE_CHOICES, default=UPDATE_AUTO)
    last_refreshed_at = models.DateTimeField(null=True, blank=True)
    sources_checked_at = models.DateTimeField(null=True, blank=True)  # last cheap website check
    locked_fields = models.JSONField(default=list, blank=True,
                                     help_text='Fields edited by a person; AI refreshes will not overwrite them')
    # API-018: details for the apps' on-device assistant (about, FAQ, amenities, policies…), events.context
    context = models.JSONField(default=dict, blank=True)

    # Fields the AI / diff engine manages
    CONTENT_FIELDS = ['name', 'description', 'address', 'latitude', 'longitude',
                      'start_time', 'end_time', 'timezone', 'context']

    def save(self, *args, **kwargs):
        from events.context import EVENT, normalize
        self.context = normalize(EVENT, self.context)
        super().save(*args, **kwargs)

    @property
    def is_live(self):
        return self.status != self.STATUS_DRAFT

    @property
    def effective_end(self):
        return self.end_time or (self.start_time + timedelta(hours=12))

    @property
    def timing(self):
        """'upcoming', 'now' or 'ended', from the event's start and end."""
        now = dj_tz.now()
        if now < self.start_time:
            return 'upcoming'
        return 'now' if now <= self.effective_end else 'ended'

    def bump_version(self, save=True):
        Event.objects.filter(pk=self.pk).update(
            content_version=models.F('content_version') + 1,
            content_updated_at=dj_tz.now(),
        )
        self.refresh_from_db(fields=['content_version', 'content_updated_at'])
        from events import push
        push.content_changed(self.pk)  # apps in the background update too (debounced silent push)

    @property
    def auto_accept_minor(self):
        return self.update_mode in (self.UPDATE_MINOR, self.UPDATE_AUTO)

    def role_for(self, user):
        """'owner', 'admin', 'contributor' or None. Site admins act as owners of every event."""
        if not user or not user.is_authenticated:
            return None
        if user.is_superuser or (self.user_id is not None and self.user_id == user.id):
            return 'owner'
        member = self.members.filter(user=user).only('role').first()
        return member.role if member else None

    def can_edit(self, user):
        """Everyone on the team edits content."""
        return self.role_for(user) is not None

    def can_manage_team(self, user):
        """The owner and event admins add and remove people, and change event-wide settings."""
        return self.role_for(user) in ('owner', EventMember.ROLE_ADMIN)

    class Meta:
        indexes = [
            models.Index(fields=['user', 'name', 'event_code', 'latitude', 'longitude']),
            # `event_code__iexact` / `name__iexact` (lookup, /join/, /p/, the DEMO checks) compare UPPER(…).
            models.Index(Upper('event_code'), name='event_code_upper_idx'),
            models.Index(Upper('name'), name='event_name_upper_idx'),
        ]

    def __str__(self):
        return self.name


class Location(CoordinatesMixin, models.Model):
    name = models.CharField(max_length=256)
    snippet = models.CharField(max_length=200, blank=True)
    description = models.CharField(max_length=1024, blank=True)
    website = models.URLField(max_length=1024, null=True, blank=True)
    image = models.ImageField(upload_to=get_file_path, blank=True, null=True)
    image_url = models.URLField(max_length=1024, blank=True, null=True)
    # Place images found automatically (stage D, events/place_images.py): where it came from, the credit and licence
    # (Wikimedia), when. `image_auto` = the current image was found by Attnd (an organizer's image never is);
    # `image_never_refetch` = the organizer removed a found one (never look again).
    image_auto = models.BooleanField(default=False)
    image_source_url = models.URLField(max_length=1024, blank=True)
    # Google's place id for this location (attnd/google.py; their terms allow keeping place ids indefinitely), '' =
    # looked up and nothing found; google_place_key = the name + address it was looked up for (changed → look again).
    google_place_id = models.CharField(max_length=300, blank=True)
    google_place_key = models.CharField(max_length=32, blank=True)
    image_credit = models.CharField(max_length=300, blank=True)  # API-022: in the bundle
    image_license = models.CharField(max_length=120, blank=True)
    image_fetched_at = models.DateTimeField(null=True, blank=True)
    image_never_refetch = models.BooleanField(default=False)
    latitude = models.DecimalField(max_digits=24, decimal_places=20, null=True, blank=True)
    longitude = models.DecimalField(max_digits=24, decimal_places=20, null=True, blank=True)
    address = models.CharField(max_length=512, blank=True)
    category = models.CharField(max_length=64, blank=True,
                                help_text='e.g. Stage, Food, Vendor, Restroom')
    tags = models.JSONField(default=list, blank=True)  # API-010, normalize_tags
    context = models.JSONField(default=dict, blank=True)  # API-018, events.context
    sort_order = models.IntegerField(default=0)
    hidden = models.BooleanField(default=False)
    locked_fields = models.JSONField(default=list, blank=True)
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)  # who created it
    event = models.ForeignKey(Event, on_delete=models.CASCADE)

    CONTENT_FIELDS = ['name', 'snippet', 'description', 'website', 'image_url',
                      'address', 'latitude', 'longitude', 'category', 'tags', 'context']

    class Meta:
        indexes = [
            models.Index(fields=['event', 'user'])
        ]
        ordering = ['sort_order', 'name']

    def save(self, *args, **kwargs):
        from events.context import LOCATION, normalize
        self.tags = normalize_tags(self.tags)
        self.context = normalize(LOCATION, self.context)
        super().save(*args, **kwargs)

    @property
    def has_coordinates(self):
        return self.latitude is not None and self.longitude is not None

    @property
    def resolved_image_url(self):
        if self.image:
            return self.image.url
        return self.image_url or None

    def __str__(self):
        return self.name


class ScheduledItem(models.Model):
    name = models.CharField(max_length=256)
    description = models.CharField(max_length=1024, blank=True)
    start_time = models.DateTimeField()
    end_time = models.DateTimeField(null=True, blank=True)
    location = models.ForeignKey(Location, on_delete=models.SET_NULL, null=True, blank=True)
    tags = models.JSONField(default=list, blank=True)
    context = models.JSONField(default=dict, blank=True)  # API-018, events.context
    hidden = models.BooleanField(default=False)
    locked_fields = models.JSONField(default=list, blank=True)
    event = models.ForeignKey(Event, on_delete=models.CASCADE)
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)  # who created it

    CONTENT_FIELDS = ['name', 'description', 'start_time', 'end_time', 'location', 'tags', 'context']

    def save(self, *args, **kwargs):
        from events.context import ITEM, normalize
        self.tags = normalize_tags(self.tags)
        self.context = normalize(ITEM, self.context)
        super().save(*args, **kwargs)

    class Meta:
        indexes = [
            models.Index(fields=['event', 'start_time']),
            models.Index(fields=['location', 'start_time']),
        ]
        ordering = ['start_time']

    def __str__(self):
        return f'{self.name} @ {self.location.name if self.location else "—"}'


# Scheduled notifications go out from `run_scheduled_tasks`, which Heroku Scheduler runs every
# 10 minutes, so scheduled send times must sit on a 10-minute mark (:00, :10, :20 …).
SEND_SLOT_MINUTES = 10


def next_send_slot(after=None):
    """The first 10-minute mark at or after `after` (default: now)."""
    after = after or dj_tz.now()
    slot = after.replace(second=0, microsecond=0)
    if after.second or after.microsecond or slot.minute % SEND_SLOT_MINUTES:
        slot += timedelta(minutes=SEND_SLOT_MINUTES - slot.minute % SEND_SLOT_MINUTES)
    return slot


def is_send_slot(value):
    local = dj_tz.localtime(value) if dj_tz.is_aware(value) else value
    return local.minute % SEND_SLOT_MINUTES == 0 and local.second == 0 and local.microsecond == 0


class Notification(models.Model):
    PRIORITY_NORMAL = 'normal'
    PRIORITY_URGENT = 'urgent'  # API-011: to every device in the event, right away, and on Live Activities
    PRIORITY_CHOICES = [(PRIORITY_NORMAL, 'Normal'), (PRIORITY_URGENT, 'Urgent')]

    title = models.CharField(max_length=60)
    body = models.CharField(max_length=256)
    send_time = models.DateTimeField()
    sent = models.BooleanField(default=False)
    sent_at = models.DateTimeField(null=True, blank=True)
    delivered_count = models.PositiveIntegerField(default=0, db_default=0)
    failed_count = models.PositiveIntegerField(default=0, db_default=0)
    priority = models.CharField(max_length=8, choices=PRIORITY_CHOICES, default=PRIORITY_NORMAL)
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)  # who created it
    event = models.ForeignKey(Event, on_delete=models.CASCADE)

    class Meta:
        indexes = [
            models.Index(fields=['event', 'user'])
        ]

    @property
    def is_urgent(self):
        return self.priority == self.PRIORITY_URGENT

    def clean(self):
        # "Send now" sends immediately and skips this; only scheduled sends are restricted.
        if not self.sent and self.send_time and not is_send_slot(self.send_time):
            from django.core.exceptions import ValidationError
            raise ValidationError({'send_time': f'Pick a time on a {SEND_SLOT_MINUTES}-minute mark '
                                                f'(e.g. 6:00, 6:10, 6:20) — that is when scheduled updates go out.'})

    def __str__(self):
        return self.title


# Devices whose id starts with this are automated tests (live integration suites, CI keys: API-008).
# They are left out of organizer audience counts, usage counters and pushes.
CI_DEVICE_PREFIX = 'ci-'


def is_ci_device(device_id):
    return str(device_id or '').startswith(CI_DEVICE_PREFIX)


class EventJoin(models.Model):
    """API-025: each device that ever joined an event, once (the free limit counts these; a device that left and
    comes back is still one of them, so a rejoin is always allowed)."""
    event = models.ForeignKey('Event', on_delete=models.CASCADE, related_name='joins')
    device_id = models.CharField(max_length=128)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['event', 'device_id'], name='event_join_once')]


class DeviceQuerySet(models.QuerySet):
    def real(self):
        """Devices of real people: without the automated test devices (``ci-…`` ids)."""
        return self.exclude(id__startswith=CI_DEVICE_PREFIX)


class Device(models.Model):
    id = models.CharField(max_length=128, primary_key=True)
    event = models.ForeignKey(Event, on_delete=models.CASCADE, null=True, blank=True)
    subscribed = models.BooleanField(default=True)
    platform = models.CharField(max_length=12)
    push_token = models.CharField(max_length=512, blank=True)
    # API-011: ActivityKit push token (hex) of the iOS Live Activity for the joined event. Cleared on leave, on
    # joining another event and when APNs reports it invalid.
    live_activity_token = models.CharField(max_length=512, blank=True)
    # API-019: the iOS App Clip (its own bundle id, so its pushes need the clip's APNs topic). Set from the
    # `app_clip` boolean in join/ and devices/ bodies; a body without it keeps the stored value.
    app_clip = models.BooleanField(default=False)
    app_version = models.CharField(max_length=32, blank=True)
    last_seen = models.DateTimeField(null=True, blank=True)
    # Newest API version this device has called (1 = legacy app builds). Used to decide when an old
    # API version can be retired: see API_CHANGES.md and `manage.py api_usage`.
    api_version = models.PositiveSmallIntegerField(null=True, blank=True)
    joined_at = models.DateTimeField(null=True, blank=True)

    objects = DeviceQuerySet.as_manager()

    class Meta:
        indexes = [
            models.Index(fields=['event'])
        ]

    def __str__(self):
        return f'{self.event} - {self.id} - {self.subscribed}'


class EventMember(models.Model):
    """People who help run an event. The owner (Event.user) is not a member.

    - admin: everything the owner can do except deleting the event or transferring it
    - contributor: edits content (details, locations, schedule, notifications, AI updates)
    """
    ROLE_ADMIN = 'admin'
    ROLE_CONTRIBUTOR = 'contributor'
    ROLE_EDITOR = ROLE_CONTRIBUTOR  # old name
    ROLE_CHOICES = [(ROLE_ADMIN, 'Admin'), (ROLE_CONTRIBUTOR, 'Contributor')]
    ROLE_CHOICES_DICT = dict(ROLE_CHOICES)

    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name='members')
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='event_memberships')
    role = models.CharField(max_length=16, choices=ROLE_CHOICES, default=ROLE_CONTRIBUTOR)
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


class EventInvite(models.Model):
    """A team invitation for an email with no account yet. It becomes an EventMember when someone
    signs up or signs in with that email (console.accounts.claim_invites)."""
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name='invites')
    email = models.EmailField()
    role = models.CharField(max_length=16, choices=EventMember.ROLE_CHOICES, default=EventMember.ROLE_CONTRIBUTOR)
    invited_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    created_at = models.DateTimeField(default=dj_tz.now)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['event', 'email'], name='invite_unique_email')]

    def __str__(self):
        return f'{self.email} → {self.event}'


def events_for_user(user):
    """Events the user owns or is a member of. Superusers see everything."""
    if not user.is_authenticated:
        return Event.objects.none()
    if user.is_superuser:
        return Event.objects.all()
    # A subquery instead of a join + DISTINCT: no duplicate rows, so annotations (counts) stay cheap and exact.
    member_of = EventMember.objects.filter(user=user).values('event_id')
    return Event.objects.filter(models.Q(user=user) | models.Q(id__in=member_of))


class UsageCount(models.Model):
    """Anonymous usage totals per 5 minutes (API-005).

    Apps report how often features were used. The server keeps only these counters: no device ID,
    IP address or anything that identifies a person is ever stored with them. `event` is null for
    app-wide counts that happen outside an event (new app installs).
    """
    event = models.ForeignKey(Event, on_delete=models.CASCADE, null=True, blank=True, related_name='usage')
    bucket = models.DateTimeField()  # UTC, start of the 5-minute window
    metric = models.CharField(max_length=32)
    detail = models.CharField(max_length=32, blank=True)  # a tab name, or a location / schedule item / notification id
    platform = models.CharField(max_length=12, blank=True)
    count = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['event', 'bucket', 'metric', 'detail', 'platform'], name='usage_bucket_unique',
                                    nulls_distinct=False),
        ]
        indexes = [models.Index(fields=['event', 'bucket']), models.Index(fields=['bucket'])]

    def __str__(self):
        return f'{self.event_id} {self.bucket:%Y-%m-%d %H:%M} {self.metric}:{self.detail} {self.platform} = {self.count}'


class PlaceImageAttempt(models.Model):
    """One look for a place image (stage D, events/place_images.py): what was tried, what came of it, what it cost
    (the daily cap sums these)."""
    location = models.ForeignKey(Location, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    source = models.CharField(max_length=24, blank=True)  # event_page / venue / wikimedia
    result = models.CharField(max_length=16)  # found / none / error / skipped
    cost_usd = models.DecimalField(max_digits=8, decimal_places=4, default=0)
    detail = models.CharField(max_length=500, blank=True)

    def __str__(self):
        return f'{self.location_id}: {self.result} ({self.source})'
