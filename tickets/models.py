"""Attnd Tickets (docs/specs/tickets_spec.md): ticket types, orders, tickets, check-ins, payouts, Wallet passes.

Money is always integer cents in the event's currency (USD for now). Stripe holds and moves every dollar: we store
only ids and amounts, never card data.
"""

import secrets
import string
import uuid

from django.contrib.auth.models import User
from django.db import models
from django.utils import timezone as dj_tz

from events.models import Event

# Human-readable codes: no 0/O, 1/I/L, so they can be read out at the door.
CODE_ALPHABET = '23456789ABCDEFGHJKMNPQRSTUVWXYZ'


def random_code(length):
    return ''.join(secrets.choice(CODE_ALPHABET) for _ in range(length))


def secret_key():
    """URL-safe secret for links (orders, tickets, Wallet passes)."""
    return secrets.token_urlsafe(24)


def normalize_email(value):
    return (value or '').strip().lower()


class PayoutAccount(models.Model):
    """An organizer's Stripe Connect Express account (hosted onboarding, hosted Express dashboard).

    Payouts on it are manual: the platform releases each event's money after the event (tickets.payouts)."""
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='payout_accounts')
    stripe_account_id = models.CharField(max_length=64, unique=True)
    email = models.EmailField(blank=True)
    livemode = models.BooleanField(default=False)
    charges_enabled = models.BooleanField(default=False)
    payouts_enabled = models.BooleanField(default=False)
    details_submitted = models.BooleanField(default=False)
    requirements_due = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(default=dj_tz.now)
    updated_at = models.DateTimeField(default=dj_tz.now)

    @property
    def ready(self):
        return self.charges_enabled and self.details_submitted

    def __str__(self):
        return self.stripe_account_id


class TicketSettings(models.Model):
    """Ticketing for one event. Ticketing needs the Studio + tickets plan (Event.plan == studio)."""
    REFUND_NONE = 'none'
    REFUND_UNTIL = 'until'
    REFUND_REQUESTS = 'requests'
    REFUND_CHOICES = [
        (REFUND_NONE, 'No refunds (except if the event is cancelled)'),
        (REFUND_UNTIL, 'Automatic refunds until a number of days before the event'),
        (REFUND_REQUESTS, 'Buyers can ask; you approve each refund'),
    ]

    event = models.OneToOneField(Event, on_delete=models.CASCADE, related_name='ticketing')
    enabled = models.BooleanField(default=False)
    on_sale = models.BooleanField(default=False)
    payout_account = models.ForeignKey(PayoutAccount, on_delete=models.SET_NULL, null=True, blank=True,
                                       related_name='events')
    currency = models.CharField(max_length=3, default='usd')
    page_text = models.TextField(blank=True)
    refund_policy = models.CharField(max_length=16, choices=REFUND_CHOICES, default=REFUND_REQUESTS)
    refund_days_before = models.PositiveSmallIntegerField(default=7)
    refund_policy_text = models.TextField(blank=True)
    faq = models.TextField(blank=True)
    # [{"id": "size", "label": "T-shirt size", "kind": "text|select|checkbox", "options": [...], "required": bool,
    #   "per_ticket": bool}]
    questions = models.JSONField(default=list, blank=True)
    max_per_order = models.PositiveSmallIntegerField(default=10)
    payout_delay_days = models.PositiveSmallIntegerField(null=True, blank=True)  # None = settings default
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancel_reason = models.TextField(blank=True)
    # Fields a person edited (AI drafts never overwrite them), like the content models.
    locked_fields = models.JSONField(default=list, blank=True)
    sold_out = models.BooleanField(default=False)  # cached for the bundle; flips bump content_version
    created_at = models.DateTimeField(default=dj_tz.now)
    updated_at = models.DateTimeField(default=dj_tz.now)

    CONTENT_FIELDS = ['page_text', 'refund_policy_text', 'faq']

    @property
    def cancelled(self):
        return self.cancelled_at is not None

    def __str__(self):
        return f'Tickets for {self.event}'


class TicketKey(models.Model):
    """The event's Ed25519 key pair for QR tokens. Doors download the public key and verify offline."""
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name='ticket_keys')
    kid = models.PositiveIntegerField()
    public_key = models.CharField(max_length=64)  # base64url (no padding) of the raw 32-byte key
    private_key = models.TextField()  # encrypted (tickets.signing)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(default=dj_tz.now)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['event', 'kid'], name='ticket_key_unique_kid')]


class CapacityPool(models.Model):
    """A cap shared by several ticket types (e.g. the venue's 500 across General and VIP)."""
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name='capacity_pools')
    name = models.CharField(max_length=120)
    capacity = models.PositiveIntegerField()

    def __str__(self):
        return f'{self.name} ({self.capacity})'


class TicketType(models.Model):
    KIND_PAID = 'paid'
    KIND_FREE = 'free'
    KIND_DONATION = 'donation'
    KIND_CHOICES = [(KIND_PAID, 'Paid'), (KIND_FREE, 'Free / RSVP'), (KIND_DONATION, 'Pay what you want')]

    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name='ticket_types')
    name = models.CharField(max_length=120)
    description = models.TextField(blank=True)
    kind = models.CharField(max_length=12, choices=KIND_CHOICES, default=KIND_PAID)
    price_cents = models.PositiveIntegerField(default=0, help_text='Ticket price; the minimum for pay what you want')
    quantity = models.PositiveIntegerField(null=True, blank=True, help_text='Empty = no limit (the pool may cap it)')
    pool = models.ForeignKey(CapacityPool, on_delete=models.SET_NULL, null=True, blank=True, related_name='types')
    sales_start = models.DateTimeField(null=True, blank=True)
    sales_end = models.DateTimeField(null=True, blank=True)
    min_per_order = models.PositiveSmallIntegerField(default=1)
    max_per_order = models.PositiveSmallIntegerField(default=10)
    hidden = models.BooleanField(default=False, help_text='Only shown with its unlock code')
    unlock_code = models.CharField(max_length=32, blank=True)
    add_on = models.BooleanField(default=False, help_text='Only with another ticket in the same order (e.g. parking)')
    valid_on = models.DateField(null=True, blank=True, help_text='Day ticket: only valid on this date')
    reentry = models.BooleanField(default=False)
    transferable = models.BooleanField(default=True)
    waitlist = models.BooleanField(default=True)
    sort_order = models.IntegerField(default=0)
    active = models.BooleanField(default=True)  # False = removed (kept for the orders that have it)
    locked_fields = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(default=dj_tz.now)

    CONTENT_FIELDS = ['name', 'description', 'kind', 'price_cents', 'quantity', 'sales_start', 'sales_end',
                      'min_per_order', 'max_per_order', 'add_on', 'reentry', 'transferable']
    # Changing these moves money or inventory: an AI draft may propose them, a person must approve each one.
    MONEY_FIELDS = ['kind', 'price_cents', 'quantity']

    class Meta:
        ordering = ['sort_order', 'id']

    @property
    def is_free(self):
        return self.kind == self.KIND_FREE or (self.kind == self.KIND_PAID and self.price_cents == 0)

    def __str__(self):
        return self.name


class PromoCode(models.Model):
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name='promo_codes')
    code = models.CharField(max_length=32)
    percent_off = models.PositiveSmallIntegerField(null=True, blank=True)
    amount_off_cents = models.PositiveIntegerField(null=True, blank=True)
    ticket_types = models.ManyToManyField(TicketType, blank=True, help_text='None selected = every ticket type')
    max_uses = models.PositiveIntegerField(null=True, blank=True)
    used_count = models.PositiveIntegerField(default=0)
    starts_at = models.DateTimeField(null=True, blank=True)
    ends_at = models.DateTimeField(null=True, blank=True)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(default=dj_tz.now)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['event', 'code'], name='promo_unique_code')]

    def save(self, *args, **kwargs):
        self.code = self.code.strip().upper()
        super().save(*args, **kwargs)

    def __str__(self):
        return self.code


class Buyer(models.Model):
    """Someone who bought or holds tickets. No account needed: a verified email is enough."""
    email = models.EmailField(unique=True)
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='buyers')
    verified_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=dj_tz.now)

    def save(self, *args, **kwargs):
        self.email = normalize_email(self.email)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.email


class Order(models.Model):
    HELD = 'held'            # cart: inventory held for TICKETS_HOLD_MINUTES while the buyer fills in details
    PENDING = 'pending'      # sent to Stripe Checkout; held until the session expires
    PAID = 'paid'
    REFUNDED = 'refunded'
    CANCELLED = 'cancelled'  # by the buyer before paying, or by the organizer
    EXPIRED = 'expired'
    STATUS_CHOICES = [(HELD, 'Held'), (PENDING, 'Awaiting payment'), (PAID, 'Paid'), (REFUNDED, 'Refunded'),
                      (CANCELLED, 'Cancelled'), (EXPIRED, 'Expired')]
    ACTIVE_HOLDS = (HELD, PENDING)

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    key = models.CharField(max_length=64, default=secret_key)
    ref = models.CharField(max_length=12, db_index=True)
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name='orders')
    buyer = models.ForeignKey(Buyer, on_delete=models.SET_NULL, null=True, blank=True, related_name='orders')
    email = models.EmailField(blank=True)
    name = models.CharField(max_length=200, blank=True)
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default=HELD, db_index=True)
    currency = models.CharField(max_length=3, default='usd')
    subtotal_cents = models.PositiveIntegerField(default=0)   # tickets before discounts
    discount_cents = models.PositiveIntegerField(default=0)
    fee_cents = models.PositiveIntegerField(default=0)        # the buyer's processing fee (gross-up)
    total_cents = models.PositiveIntegerField(default=0)      # what the buyer pays
    refunded_cents = models.PositiveIntegerField(default=0)   # ticket money refunded
    fee_refunded_cents = models.PositiveIntegerField(default=0)
    promo = models.ForeignKey(PromoCode, on_delete=models.SET_NULL, null=True, blank=True, related_name='orders')
    answers = models.JSONField(default=dict, blank=True)
    comp = models.BooleanField(default=False)
    source = models.CharField(max_length=16, default='web')  # web / site / embed / comp / app
    expires_at = models.DateTimeField(null=True, blank=True)
    stripe_session_id = models.CharField(max_length=128, blank=True, db_index=True)
    stripe_payment_intent_id = models.CharField(max_length=128, blank=True, db_index=True)
    stripe_charge_id = models.CharField(max_length=128, blank=True, db_index=True)
    stripe_transfer_id = models.CharField(max_length=128, blank=True)
    stripe_account_id = models.CharField(max_length=64, blank=True)  # where the ticket money went
    dispute_status = models.CharField(max_length=32, blank=True)
    note = models.TextField(blank=True)  # organizer's note
    created_at = models.DateTimeField(default=dj_tz.now)
    paid_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(default=dj_tz.now)

    class Meta:
        ordering = ['-created_at']
        constraints = [models.UniqueConstraint(fields=['event', 'ref'], name='order_unique_ref')]
        indexes = [models.Index(fields=['event', 'status']), models.Index(fields=['email'])]

    @property
    def is_free(self):
        return self.total_cents == 0

    @property
    def net_cents(self):
        """Ticket money the organizer keeps (after discounts and refunds)."""
        return max(0, self.subtotal_cents - self.discount_cents - self.refunded_cents)

    @property
    def refundable_cents(self):
        return self.net_cents

    def save(self, *args, **kwargs):
        self.email = normalize_email(self.email)
        if not self.ref:
            for _ in range(20):
                ref = random_code(6)
                if not Order.objects.filter(event_id=self.event_id, ref=ref).exists():
                    self.ref = ref
                    break
        self.updated_at = dj_tz.now()
        super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.ref} ({self.status})'


class OrderItem(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='items')
    ticket_type = models.ForeignKey(TicketType, on_delete=models.PROTECT, related_name='order_items')
    quantity = models.PositiveSmallIntegerField()
    unit_price_cents = models.PositiveIntegerField()  # before discounts (donations: the chosen amount)
    discount_cents = models.PositiveIntegerField(default=0)  # for the whole line

    @property
    def line_cents(self):
        return self.quantity * self.unit_price_cents - self.discount_cents


class Ticket(models.Model):
    VALID = 'valid'
    REFUNDED = 'refunded'
    VOID = 'void'  # cancelled by the organizer, a lost dispute, or the event was cancelled
    STATUS_CHOICES = [(VALID, 'Valid'), (REFUNDED, 'Refunded'), (VOID, 'Void')]

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    key = models.CharField(max_length=64, default=secret_key)
    code = models.CharField(max_length=12, unique=True)  # "K7Q29XPM", shown as K7Q2-9XPM
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='tickets')
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name='tickets')
    ticket_type = models.ForeignKey(TicketType, on_delete=models.PROTECT, related_name='tickets')
    holder_name = models.CharField(max_length=200, blank=True)
    holder_email = models.EmailField(blank=True, db_index=True)
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default=VALID, db_index=True)
    # Bumped on every transfer / refund / void: older QR codes stop working (revocation list).
    version = models.PositiveIntegerField(default=1)
    price_cents = models.PositiveIntegerField(default=0)  # ticket money for this ticket (after discount)
    fee_cents = models.PositiveIntegerField(default=0)    # its share of the order's processing fee
    refunded_cents = models.PositiveIntegerField(default=0)
    answers = models.JSONField(default=dict, blank=True)
    checked_in_at = models.DateTimeField(null=True, blank=True)
    checked_in_gate = models.CharField(max_length=64, blank=True)
    checked_in_device = models.CharField(max_length=128, blank=True)
    inside = models.BooleanField(default=False)  # re-entry: currently in (last scan was "in")
    transfer_count = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(default=dj_tz.now)
    updated_at = models.DateTimeField(default=dj_tz.now)

    class Meta:
        ordering = ['id']
        indexes = [models.Index(fields=['event', 'status'])]

    @property
    def display_code(self):
        return f'{self.code[:4]}-{self.code[4:]}' if len(self.code) == 8 else self.code

    def save(self, *args, **kwargs):
        self.holder_email = normalize_email(self.holder_email)
        if not self.code:
            for _ in range(20):
                code = random_code(8)
                if not Ticket.objects.filter(code=code).exists():
                    self.code = code
                    break
        self.updated_at = dj_tz.now()
        super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.display_code} {self.ticket_type.name}'


class TicketTransfer(models.Model):
    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name='transfers')
    from_email = models.EmailField(blank=True)
    to_email = models.EmailField()
    to_name = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(default=dj_tz.now)


class Revocation(models.Model):
    """QR tokens of `ticket` with a version <= `version` no longer admit anyone. Doors download these (by id cursor)."""
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name='ticket_revocations')
    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name='revocations')
    version = models.PositiveIntegerField()
    reason = models.CharField(max_length=16)  # transfer / refund / void / reissue
    created_at = models.DateTimeField(default=dj_tz.now)

    class Meta:
        ordering = ['id']


class CheckIn(models.Model):
    """One scan at the door, as a door device recorded it (possibly offline, synced later)."""
    OK = 'ok'
    DUPLICATE = 'duplicate'  # the ticket was already in (earliest scan wins; this one is flagged)
    REVOKED = 'revoked'
    VOID = 'void'
    INVALID = 'invalid'
    WRONG_DAY = 'wrong_day'
    RESULTS = [(OK, 'Admitted'), (DUPLICATE, 'Already checked in'), (REVOKED, 'Old QR code'), (VOID, 'Refunded / void'),
               (INVALID, 'Not a ticket for this event'), (WRONG_DAY, 'Not valid today')]
    IN, OUT = 'in', 'out'

    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name='checkins')
    ticket = models.ForeignKey(Ticket, on_delete=models.SET_NULL, null=True, blank=True, related_name='checkins')
    client_id = models.CharField(max_length=64)  # the device's id for the scan: makes uploads idempotent
    device_id = models.CharField(max_length=128, blank=True)
    gate = models.CharField(max_length=64, blank=True)
    kind = models.CharField(max_length=4, default=IN)
    at = models.DateTimeField()  # when it was scanned (device clock)
    received_at = models.DateTimeField(default=dj_tz.now)
    token_version = models.PositiveIntegerField(null=True, blank=True)
    result = models.CharField(max_length=12, choices=RESULTS)
    device_result = models.CharField(max_length=12, blank=True)  # what the device showed offline
    staff_email = models.EmailField(blank=True)
    flagged = models.BooleanField(default=False)  # admitted offline but the server disagrees (duplicate, revoked)

    class Meta:
        ordering = ['at', 'id']
        constraints = [models.UniqueConstraint(fields=['event', 'client_id'], name='checkin_unique_client_id')]
        indexes = [models.Index(fields=['event', 'at']), models.Index(fields=['ticket', 'at'])]


class VerificationCode(models.Model):
    """A 6-digit code emailed to prove an email address (buyers: order lookup, My tickets; door staff)."""
    PURPOSE_BUYER = 'buyer'
    PURPOSE_DOOR = 'door'
    email = models.EmailField(db_index=True)
    purpose = models.CharField(max_length=8)
    code_hash = models.CharField(max_length=64)
    attempts = models.PositiveSmallIntegerField(default=0)
    used = models.BooleanField(default=False)
    created_at = models.DateTimeField(default=dj_tz.now)
    expires_at = models.DateTimeField()
    # Dev/CI only (TICKETS_TEST_EMAIL_DOMAIN addresses): the code itself, for the apps' test inbox endpoint.
    test_code = models.CharField(max_length=8, blank=True)


class AccessToken(models.Model):
    """Bearer tokens for the apps: a buyer's "My tickets" (one email) or door staff (one email, the events they work)."""
    KIND_BUYER = 'buyer'
    KIND_DOOR = 'door'
    kind = models.CharField(max_length=8)
    email = models.EmailField(db_index=True)
    token_hash = models.CharField(max_length=64, unique=True)
    label = models.CharField(max_length=120, blank=True)  # device name the app sent
    created_at = models.DateTimeField(default=dj_tz.now)
    expires_at = models.DateTimeField()
    last_used_at = models.DateTimeField(null=True, blank=True)
    revoked = models.BooleanField(default=False)


class DoorStaff(models.Model):
    """People who scan tickets at the door. They sign in to the apps' door mode with this email; no console access."""
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name='door_staff')
    email = models.EmailField()
    name = models.CharField(max_length=120, blank=True)
    invited_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    created_at = models.DateTimeField(default=dj_tz.now)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['event', 'email'], name='door_staff_unique_email')]

    def save(self, *args, **kwargs):
        self.email = normalize_email(self.email)
        super().save(*args, **kwargs)


class StripeEvent(models.Model):
    """Every webhook event we processed (idempotency: Stripe retries and may send an event twice)."""
    id = models.CharField(max_length=128, primary_key=True)
    type = models.CharField(max_length=64)
    livemode = models.BooleanField(default=False)
    account = models.CharField(max_length=64, blank=True)
    received_at = models.DateTimeField(default=dj_tz.now)
    processed_at = models.DateTimeField(null=True, blank=True)
    error = models.TextField(blank=True)


class Refund(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='refunds')
    amount_cents = models.PositiveIntegerField()       # ticket money
    fee_cents = models.PositiveIntegerField(default=0)  # processing fee (only when the event is cancelled)
    tickets = models.ManyToManyField(Ticket, blank=True, related_name='refunds')
    reason = models.CharField(max_length=200, blank=True)
    stripe_refund_id = models.CharField(max_length=128, blank=True)
    stripe_reversal_id = models.CharField(max_length=128, blank=True)
    status = models.CharField(max_length=16, default='pending')  # pending / succeeded / failed
    error = models.TextField(blank=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    created_at = models.DateTimeField(default=dj_tz.now)


class RefundRequest(models.Model):
    PENDING, APPROVED, DECLINED = 'pending', 'approved', 'declined'
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='refund_requests')
    tickets = models.ManyToManyField(Ticket, blank=True, related_name='refund_requests')
    reason = models.TextField(blank=True)
    status = models.CharField(max_length=12, default=PENDING)
    created_at = models.DateTimeField(default=dj_tz.now)
    decided_at = models.DateTimeField(null=True, blank=True)
    decided_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')


class Payout(models.Model):
    """One event's ticket money, released to the organizer's bank after the event (manual payouts)."""
    SCHEDULED, RELEASED, FAILED, NOTHING = 'scheduled', 'released', 'failed', 'nothing'
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name='payouts')
    account = models.ForeignKey(PayoutAccount, on_delete=models.SET_NULL, null=True, blank=True)
    amount_cents = models.PositiveIntegerField(default=0)
    currency = models.CharField(max_length=3, default='usd')
    status = models.CharField(max_length=12, default=SCHEDULED)
    release_at = models.DateTimeField()
    released_at = models.DateTimeField(null=True, blank=True)
    stripe_payout_id = models.CharField(max_length=128, blank=True)
    stripe_status = models.CharField(max_length=32, blank=True)
    error = models.TextField(blank=True)
    created_at = models.DateTimeField(default=dj_tz.now)

    class Meta:
        ordering = ['-release_at']


class WaitlistEntry(models.Model):
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name='waitlist')
    ticket_type = models.ForeignKey(TicketType, on_delete=models.CASCADE, related_name='waitlist_entries')
    email = models.EmailField()
    name = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(default=dj_tz.now)
    notified_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['created_at']
        constraints = [models.UniqueConstraint(fields=['ticket_type', 'email'], name='waitlist_unique_email')]


class WalletPass(models.Model):
    """A ticket added to Apple Wallet (pass + web service registrations) or saved to Google Wallet."""
    APPLE, GOOGLE = 'apple', 'google'
    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name='wallet_passes')
    platform = models.CharField(max_length=8)
    serial = models.CharField(max_length=64, unique=True)
    auth_token = models.CharField(max_length=64, default=secret_key)
    # A transfer retires the old holder's passes (they show as voided); the new holder gets new ones.
    voided = models.BooleanField(default=False)
    updated_at = models.DateTimeField(default=dj_tz.now)  # the pass's "last updated" tag
    created_at = models.DateTimeField(default=dj_tz.now)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['ticket', 'platform'], condition=models.Q(voided=False),
                                               name='wallet_pass_unique_current')]


class WalletRegistration(models.Model):
    """An iPhone that added an Apple Wallet pass and wants update pushes (PassKit web service)."""
    wallet_pass = models.ForeignKey(WalletPass, on_delete=models.CASCADE, related_name='registrations')
    device_library_id = models.CharField(max_length=128, db_index=True)
    push_token = models.CharField(max_length=256)
    created_at = models.DateTimeField(default=dj_tz.now)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['wallet_pass', 'device_library_id'],
                                               name='wallet_registration_unique')]


class HolderMessage(models.Model):
    """An email the organizer sent to every ticket holder."""
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name='holder_messages')
    subject = models.CharField(max_length=160)
    body = models.TextField()
    recipients = models.PositiveIntegerField(default=0)
    sent_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    sent_at = models.DateTimeField(default=dj_tz.now)


# Short random strings for the console's code generator.
def suggest_code(length=8):
    return ''.join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(length))
