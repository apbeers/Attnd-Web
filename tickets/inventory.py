"""What's on sale, how many are left, promo / unlock codes and 10-minute cart holds.

Holds are Orders in status held/pending with `expires_at` in the future. Creating one locks the ticket types (and
their shared pools) with SELECT … FOR UPDATE in id order, counts what is sold or held, and only then inserts the
order, so two buyers can never both get the last ticket (tests_inventory runs that race on Postgres).
"""

from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.utils import timezone

from events.models import Event
from tickets import money
from tickets.models import CapacityPool, Order, OrderItem, PromoCode, Ticket, TicketSettings, TicketType

MAX_DONATION_CENTS = 10_000_00


class CartError(Exception):
    """A problem to show the buyer (the message is safe to display)."""


def ticketing(event):
    obj, _ = TicketSettings.objects.get_or_create(event=event)
    return obj


def plan_allows(event):
    """Ticketing is part of the $499 Studio + tickets plan."""
    return event.plan == Event.PLAN_STUDIO


def sales_state(event, ts=None):
    """('open', '') or (state, reason) — why tickets can't be bought right now."""
    ts = ts or ticketing(event)
    if not plan_allows(event) or not ts.enabled:
        return 'off', 'Tickets aren’t sold through Attnd for this event.'
    if ts.cancelled:
        return 'cancelled', 'This event was cancelled.'
    if event.status != Event.STATUS_PUBLISHED:
        return 'closed', 'Ticket sales have closed.'
    if not ts.on_sale:
        return 'paused', 'Tickets aren’t on sale yet.'
    if timezone.now() > event.effective_end:
        return 'ended', 'This event has ended.'
    return 'open', ''


def paid_ready(event, ts=None):
    """Paid tickets need Stripe and the organizer's completed Connect onboarding."""
    from tickets import stripe_api
    ts = ts or ticketing(event)
    account = ts.payout_account
    return stripe_api.configured() and account is not None and account.ready


def active_hold_q(now=None, prefix=''):
    now = now or timezone.now()
    return Q(**{f'{prefix}status__in': Order.ACTIVE_HOLDS, f'{prefix}expires_at__gt': now})


def reserved_by_type(event, now=None):
    """{type id: tickets sold (valid) + tickets in active holds}."""
    now = now or timezone.now()
    counts = {}
    for type_id, n in (Ticket.objects.filter(event=event, status=Ticket.VALID).values('ticket_type_id')
                       .annotate(n=Count('id')).values_list('ticket_type_id', 'n')):
        counts[type_id] = counts.get(type_id, 0) + n
    held = (OrderItem.objects.filter(order__event=event).filter(active_hold_q(now, 'order__'))
            .values('ticket_type_id').annotate(n=Sum('quantity')).values_list('ticket_type_id', 'n'))
    for type_id, n in held:
        counts[type_id] = counts.get(type_id, 0) + (n or 0)
    return counts


def availability(event, now=None, reserved=None):
    """{type id: tickets left (None = unlimited)} for every type of the event, pools included."""
    reserved = reserved if reserved is not None else reserved_by_type(event, now)
    types = list(TicketType.objects.filter(event=event))
    pool_used = {}
    for t in types:
        if t.pool_id:
            pool_used[t.pool_id] = pool_used.get(t.pool_id, 0) + reserved.get(t.id, 0)
    pools = {p.id: p for p in CapacityPool.objects.filter(event=event)}
    left = {}
    for t in types:
        own = None if t.quantity is None else max(0, t.quantity - reserved.get(t.id, 0))
        if t.pool_id and t.pool_id in pools:
            shared = max(0, pools[t.pool_id].capacity - pool_used.get(t.pool_id, 0))
            own = shared if own is None else min(own, shared)
        left[t.id] = own
    return left


def window(ticket_type, now=None):
    now = now or timezone.now()
    if ticket_type.sales_start and now < ticket_type.sales_start:
        return 'before'
    if ticket_type.sales_end and now > ticket_type.sales_end:
        return 'after'
    return 'open'


def unlocked(ticket_type, codes):
    if not ticket_type.hidden:
        return True
    wanted = (ticket_type.unlock_code or '').strip().upper()
    return bool(wanted) and wanted in {(c or '').strip().upper() for c in codes}


def listing(event, unlock_codes=(), now=None):
    """The ticket types a buyer may see, with their state, for the ticket page, Studio sites and the bundle."""
    now = now or timezone.now()
    left = availability(event, now)
    rows = []
    for t in TicketType.objects.filter(event=event, active=True).order_by('sort_order', 'id'):
        if not unlocked(t, unlock_codes):
            continue
        remaining = left.get(t.id)
        state = window(t, now)
        if state == 'open' and remaining == 0:
            state = 'sold_out'
        rows.append({'type': t, 'remaining': remaining, 'state': state, 'all_in_cents': money.all_in(t.price_cents),
                     'unlocked': t.hidden})
    return rows


def sold_out(event, now=None):
    """Every ticket type you can buy on its own (not add-ons, not hidden) is sold out."""
    rows = [r for r in listing(event, now=now) if not r['type'].add_on and not r['type'].hidden]
    return bool(rows) and all(r['state'] == 'sold_out' for r in rows)


def refresh_sold_out(event):
    """Cache the sold-out flag for the bundle; bump content_version only when it flips (never per sale)."""
    ts = ticketing(event)
    now_sold_out = sold_out(event)
    if ts.sold_out != now_sold_out:
        TicketSettings.objects.filter(pk=ts.pk).update(sold_out=now_sold_out)
        event.bump_version()
        return True
    return False


# ---------------------------------------------------------------------------
# Promo codes
# ---------------------------------------------------------------------------

def find_promo(event, code, now=None):
    code = (code or '').strip().upper()
    if not code:
        return None
    promo = PromoCode.objects.filter(event=event, code=code, active=True).first()
    now = now or timezone.now()
    if promo is None or (promo.starts_at and now < promo.starts_at) or (promo.ends_at and now > promo.ends_at):
        raise CartError('That promo code isn’t valid.')
    return promo


def promo_uses(promo, now=None):
    return Order.objects.filter(promo=promo).filter(Q(status=Order.PAID) | active_hold_q(now)).count()


def discount(promo, ticket_type, unit_cents, quantity):
    """The discount (cents) on one cart line."""
    if promo is None or unit_cents <= 0:
        return 0
    applies = list(promo.ticket_types.values_list('id', flat=True))
    if applies and ticket_type.id not in applies:
        return 0
    if promo.percent_off:
        per = unit_cents * min(promo.percent_off, 100) // 100
    else:
        per = min(promo.amount_off_cents or 0, unit_cents)
    return per * quantity


# ---------------------------------------------------------------------------
# Holds
# ---------------------------------------------------------------------------

def _validate_lines(event, ts, lines, unlock_codes, now):
    """lines: [(type id, quantity, amount cents or None)] → [(TicketType, qty, unit cents)] (no locks yet)."""
    wanted = {}
    for type_id, quantity, amount in lines:
        try:
            type_id, quantity = int(type_id), int(quantity)
        except (TypeError, ValueError):
            raise CartError('Pick how many tickets you want.')
        if quantity < 0:
            raise CartError('Pick how many tickets you want.')
        if quantity:
            wanted[type_id] = (wanted.get(type_id, (0, None))[0] + quantity, amount)
    if not wanted:
        raise CartError('Pick at least one ticket.')
    total = sum(q for q, _ in wanted.values())
    limit = min(ts.max_per_order or settings.TICKETS_MAX_PER_ORDER, settings.TICKETS_MAX_PER_ORDER)
    if total > limit:
        raise CartError(f'You can buy up to {limit} tickets per order.')
    types = {t.id: t for t in TicketType.objects.filter(event=event, id__in=wanted, active=True)}
    out = []
    for type_id, (quantity, amount) in wanted.items():
        t = types.get(type_id)
        if t is None or not unlocked(t, unlock_codes):
            raise CartError('One of those tickets isn’t available.')
        state = window(t, now)
        if state == 'before':
            raise CartError(f'{t.name} isn’t on sale yet.')
        if state == 'after':
            raise CartError(f'Sales for {t.name} have ended.')
        if quantity < t.min_per_order:
            raise CartError(f'{t.name}: at least {t.min_per_order} per order.')
        if quantity > t.max_per_order:
            raise CartError(f'{t.name}: at most {t.max_per_order} per order.')
        if t.kind == TicketType.KIND_DONATION:
            try:
                unit = money.parse_dollars(amount) if not isinstance(amount, int) else amount
            except (ValueError, ArithmeticError):
                raise CartError(f'{t.name}: enter an amount.')
            if unit < t.price_cents:
                raise CartError(f'{t.name}: the minimum is {money.fmt(t.price_cents, ts.currency)}.')
            if unit > MAX_DONATION_CENTS:
                raise CartError(f'{t.name}: that amount is too large.')
        elif t.kind == TicketType.KIND_FREE:
            unit = 0
        else:
            unit = t.price_cents
        out.append((t, quantity, unit))
    if all(t.add_on for t, _, _ in out):
        raise CartError('Add-ons can only be bought with a ticket.')
    return sorted(out, key=lambda line: line[0].sort_order)


def create_hold(event, lines, *, promo_code='', unlock_codes=(), source='web', email='', name='', now=None):
    """Hold the tickets for TICKETS_HOLD_MINUTES and return the Order (status held). Raises CartError."""
    now = now or timezone.now()
    ts = ticketing(event)
    state, reason = sales_state(event, ts)
    if state != 'open':
        raise CartError(reason)
    lines = _validate_lines(event, ts, lines, unlock_codes, now)
    promo = find_promo(event, promo_code, now) if promo_code else None
    paid = any(unit > 0 for _, _, unit in lines)
    if paid and not paid_ready(event, ts):
        raise CartError('Paid tickets aren’t available yet. Please check back soon.')

    with transaction.atomic():
        type_ids = sorted({t.id for t, _, _ in lines})
        locked = {t.id: t for t in TicketType.objects.select_for_update().filter(id__in=type_ids).order_by('id')}
        pool_ids = sorted({t.pool_id for t in locked.values() if t.pool_id})
        if pool_ids:
            list(CapacityPool.objects.select_for_update().filter(id__in=pool_ids).order_by('id'))
        if promo is not None:
            promo = PromoCode.objects.select_for_update().get(pk=promo.pk)
            if promo.max_uses is not None and promo_uses(promo, now) >= promo.max_uses:
                raise CartError('That promo code has been used up.')
        reserved = reserved_by_type(event, now)
        left = availability(event, now, reserved)
        # Shared pools: the lines of one pool together must fit.
        per_pool = {}
        for t, quantity, _ in lines:
            remaining = left.get(t.id)
            if remaining is not None and quantity > remaining:
                raise CartError(f'Only {remaining} {t.name} left.' if remaining else f'{t.name} is sold out.')
            if t.pool_id:
                per_pool[t.pool_id] = per_pool.get(t.pool_id, 0) + quantity
        for pool in CapacityPool.objects.filter(id__in=per_pool):
            used = sum(reserved.get(type_id, 0) for type_id in pool.types.values_list('id', flat=True))
            if used + per_pool[pool.id] > pool.capacity:
                raise CartError('Not enough tickets left for that combination.')

        order = Order(event=event, status=Order.HELD, currency=ts.currency, promo=promo, source=source[:16],
                      email=email, name=name[:200], expires_at=now + timedelta(minutes=settings.TICKETS_HOLD_MINUTES))
        order.save()
        subtotal = discount_total = 0
        for t, quantity, unit in lines:
            off = discount(promo, t, unit, quantity)
            OrderItem.objects.create(order=order, ticket_type=t, quantity=quantity, unit_price_cents=unit,
                                     discount_cents=off)
            subtotal += unit * quantity
            discount_total += off
        set_totals(order, subtotal, discount_total)
    return order


def set_totals(order, subtotal, discount_total):
    order.subtotal_cents = subtotal
    order.discount_cents = min(discount_total, subtotal)
    payable = subtotal - order.discount_cents
    order.fee_cents = money.fee_for(payable)
    order.total_cents = payable + order.fee_cents
    order.save(update_fields=['subtotal_cents', 'discount_cents', 'fee_cents', 'total_cents', 'updated_at'])


def cancel_hold(order):
    """The buyer gave up (or started over): free the tickets now."""
    if order.status in Order.ACTIVE_HOLDS:
        Order.objects.filter(pk=order.pk, status__in=Order.ACTIVE_HOLDS).update(status=Order.CANCELLED,
                                                                                 updated_at=timezone.now())
        order.refresh_from_db()
        from tickets import payments
        payments.expire_checkout(order)
        return True
    return False


def expire_holds(now=None):
    """Mark lapsed holds expired (scheduled task); expire their Stripe sessions; tell waitlists. Returns the count."""
    now = now or timezone.now()
    lapsed = list(Order.objects.filter(status__in=Order.ACTIVE_HOLDS, expires_at__lte=now).select_related('event'))
    events = set()
    for order in lapsed:
        if Order.objects.filter(pk=order.pk, status__in=Order.ACTIVE_HOLDS).update(
                status=Order.EXPIRED, updated_at=now):
            events.add(order.event)
            if order.stripe_session_id:
                from tickets import payments
                payments.expire_checkout(order)
    from tickets import waitlist
    for event in events:
        waitlist.notify_if_available(event)
        refresh_sold_out(event)
    return len(lapsed)
