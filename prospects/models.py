"""Prospects (staff only): events in a region that suit the app, the people who run them, and the runs that found
them. A sales pipeline: free app first, then Attnd Studio. Attnd never emails prospects itself."""

from decimal import Decimal

from django.contrib.auth.models import User
from django.db import models
from django.utils import timezone

from events.models import Event

DEFAULT_REGION = ('Dallas–Fort Worth, Texas: Dallas, Fort Worth, Arlington, Denton, Plano, Frisco, McKinney, Irving, '
                  'Grapevine, Garland, Richardson, Carrollton, Lewisville, Mesquite, Grand Prairie, Southlake, '
                  'Addison, Rockwall, Allen, Deep Ellum, Bishop Arts, the Stockyards; Dallas, Tarrant, Collin, '
                  'Denton, Rockwall and Ellis counties')

DEFAULT_THEMES = '\n'.join([
    'art walks', 'gallery nights', 'street festivals', 'food & music festivals', 'farmers & artisan markets',
    'studio tours', 'home & garden tours', 'holiday light walks', 'bar, brewery & wine crawls',
    'first / third Thursday or Friday events', 'parades', 'fairs', 'block parties', 'open streets',
    'cultural festivals',
])


class ProspectSettings(models.Model):
    """One row (pk 1): what a discovery run looks for, and its schedule."""
    region = models.TextField(default=DEFAULT_REGION)
    timezone = models.CharField(max_length=64, default='America/Chicago',
                                help_text="The region's IANA time zone (test events are imported in it)")
    themes = models.TextField(default=DEFAULT_THEMES, help_text='One kind of event per line')
    horizon_days = models.PositiveIntegerField(default=90)
    max_candidates = models.PositiveIntegerField(default=40, help_text='New events read per run (each costs AI)')
    schedule_enabled = models.BooleanField(default=False)
    interval_days = models.PositiveIntegerField(default=7)
    last_run_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name_plural = 'prospect settings'

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    @property
    def theme_list(self):
        return [t.strip() for t in self.themes.splitlines() if t.strip()]

    def __str__(self):
        return 'Prospect settings'


class ProspectRun(models.Model):
    MANUAL, SCHEDULED = 'manual', 'scheduled'
    TRIGGER_CHOICES = [(MANUAL, 'Run now'), (SCHEDULED, 'Schedule')]
    QUEUED, RUNNING, DONE, FAILED, SKIPPED = 'queued', 'running', 'done', 'failed', 'skipped'
    STATUS_CHOICES = [(QUEUED, 'Queued'), (RUNNING, 'Running'), (DONE, 'Done'), (FAILED, 'Failed'),
                      (SKIPPED, 'Skipped')]

    trigger = models.CharField(max_length=16, choices=TRIGGER_CHOICES, default=MANUAL)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=QUEUED, db_index=True)
    started_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    found = models.PositiveIntegerField(default=0)
    new = models.PositiveIntegerField(default=0)
    updated = models.PositiveIntegerField(default=0)
    skipped = models.PositiveIntegerField(default=0)
    cost_usd = models.DecimalField(max_digits=8, decimal_places=4, default=Decimal('0'))
    log = models.TextField(blank=True)
    error = models.TextField(blank=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'Prospect run {self.pk} [{self.status}]'

    @property
    def is_finished(self):
        return self.status in (self.DONE, self.FAILED, self.SKIPPED)


class Prospect(models.Model):
    NEW, REVIEWING, TEST_EVENT, CONTACTED, INTERESTED, WON, LOST, DISMISSED = (
        'new', 'reviewing', 'test_event_created', 'contacted', 'interested', 'won', 'lost', 'dismissed')
    CLAIMED = 'claimed'  # its organizer claimed the test event and staff approved it (home page "Claim it")
    STATUS_CHOICES = [(NEW, 'New'), (REVIEWING, 'Reviewing'), (TEST_EVENT, 'Test event created'),
                      (CONTACTED, 'Contacted'), (INTERESTED, 'Interested'), (CLAIMED, 'Claimed'), (WON, 'Won'),
                      (LOST, 'Lost'), (DISMISSED, 'Dismissed')]

    name = models.CharField(max_length=256)
    organizer = models.CharField(max_length=256, blank=True, help_text='Organizing person or organization')
    summary = models.TextField(blank=True)
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    city = models.CharField(max_length=128, blank=True)
    venue = models.CharField(max_length=256, blank=True, help_text='Venue or area')
    urls = models.JSONField(default=list, blank=True)
    fit_score = models.PositiveSmallIntegerField(default=0, db_index=True)
    fit_reasons = models.JSONField(default=list, blank=True)
    traits = models.JSONField(default=list, blank=True)
    status = models.CharField(max_length=24, choices=STATUS_CHOICES, default=NEW, db_index=True)
    notes = models.TextField(blank=True)
    # Dedupe: the primary URL (host without www + path, lower case) and the name (normalised) + year.
    url_key = models.CharField(max_length=512, blank=True, db_index=True)
    name_key = models.CharField(max_length=300, blank=True, db_index=True)
    first_seen_run = models.ForeignKey(ProspectRun, on_delete=models.SET_NULL, null=True, blank=True,
                                       related_name='first_seen')
    last_seen_run = models.ForeignKey(ProspectRun, on_delete=models.SET_NULL, null=True, blank=True,
                                      related_name='last_seen')
    event = models.ForeignKey(Event, on_delete=models.SET_NULL, null=True, blank=True, related_name='prospects',
                              help_text='The testing-only event made from it')
    ingest_job = models.ForeignKey('scrape.IngestJob', on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name='prospects')
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-fit_score', 'start_date', 'id']

    def __str__(self):
        return self.name

    @property
    def primary_url(self):
        return self.urls[0] if self.urls else ''

    @property
    def top_contact(self):
        contacts = [c for c in self.contacts.all() if not c.do_not_contact]
        contacts.sort(key=lambda c: (not c.email, not c.name, c.pk))
        return contacts[0] if contacts else None

    @property
    def trait_labels(self):
        return [str(t).replace('_', ' ') for t in self.traits or []]

    @property
    def fit_tone(self):
        return 'success' if self.fit_score >= 70 else 'warning' if self.fit_score >= 40 else 'muted'


class ProspectContact(models.Model):
    """Organizer contact details published on the event's own pages (source_url is where it appeared)."""
    prospect = models.ForeignKey(Prospect, on_delete=models.CASCADE, related_name='contacts')
    name = models.CharField(max_length=256, blank=True)
    role = models.CharField(max_length=256, blank=True)
    organization = models.CharField(max_length=256, blank=True)
    email = models.EmailField(max_length=254, blank=True)
    phone = models.CharField(max_length=64, blank=True)
    website = models.URLField(max_length=1024, blank=True)
    profile_url = models.URLField(max_length=1024, blank=True, help_text='Social or profile page')
    source_url = models.URLField(max_length=1024)
    do_not_contact = models.BooleanField(default=False)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['id']

    def __str__(self):
        return self.name or self.organization or self.email


class ProspectActivity(models.Model):
    prospect = models.ForeignKey(Prospect, on_delete=models.CASCADE, related_name='activity')
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    text = models.CharField(max_length=512)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['-created_at', '-id']
        verbose_name_plural = 'prospect activity'


class ClaimRequest(models.Model):
    """"Is this your event? Claim it" on the home page's "Happening near you" cards: a signed-in person asks for a
    prospect's test event. Never automatic: staff approve it in Prospects → Claims, which hands the event over."""
    PENDING, APPROVED, DECLINED = 'pending', 'approved', 'declined'
    STATUS_CHOICES = [(PENDING, 'Waiting for staff'), (APPROVED, 'Approved'), (DECLINED, 'Declined')]

    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name='claim_requests')
    prospect = models.ForeignKey(Prospect, on_delete=models.SET_NULL, null=True, blank=True, related_name='claims')
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="event_claims")
    email = models.EmailField()
    role = models.CharField(max_length=200, help_text='Their role at the event')
    note = models.TextField(blank=True)
    # An automatic hint for staff: the email's domain matches the event's website or a prospect contact.
    domain_match = models.CharField(max_length=200, blank=True)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=PENDING, db_index=True)
    decided_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name='+')
    decided_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.email} → {self.event_id} ({self.status})'


class PitchDraft(models.Model):
    """An outreach email drafted for a prospect (prospects/pitch.py). A draft only: Attnd never sends it; a person
    copies it into their own mail and adds the business's mailing address (CAN-SPAM)."""
    prospect = models.ForeignKey(Prospect, on_delete=models.CASCADE, related_name='pitch_drafts')
    contact = models.ForeignKey(ProspectContact, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    DRAFT, SENT, REPLIED, DECLINED, BOUNCED = 'draft', 'sent', 'replied', 'declined', 'bounced'
    STATUSES = [(DRAFT, 'Draft'), (SENT, 'Sent (by hand)'), (REPLIED, 'Replied'), (DECLINED, 'Declined'),
                (BOUNCED, 'Bounced')]
    to = models.EmailField(max_length=254, blank=True)
    subject = models.CharField(max_length=200)
    body = models.TextField()
    status = models.CharField(max_length=10, choices=STATUSES, default=DRAFT)
    status_at = models.DateTimeField(null=True, blank=True, help_text='When it was sent, answered…')
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['-created_at', '-id']
