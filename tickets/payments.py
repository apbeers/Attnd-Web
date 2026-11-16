"""Money with Stripe: Connect Express onboarding, Checkout, issuing tickets, refunds, payouts after the event, webhooks.

Design (spec: Stripe design):
- Organizers get Stripe Connect **Express** accounts (hosted onboarding + hosted Express dashboard) whose payouts are
  **manual**: the platform releases each event's ticket money `payout_delay_days` after the event ends.
- One Checkout Session per order (attnd.app only). It is a **destination charge** `on_behalf_of` the organizer:
  `transfer_data.destination` = their account and `application_fee_amount` = the buyer's processing fee, which pays
  the Stripe fee billed to the platform, so the organizer receives the full ticket price.
- Refunds return the ticket price (the charge is refunded and exactly that much of the transfer is reversed); the
  processing fee is refunded only when the event is cancelled (Attnd absorbs Stripe's kept fee then).
- Every webhook is idempotent (StripeEvent rows; orders are locked and checked before tickets are issued).
"""

import logging
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import F
from django.urls import reverse
from django.utils import timezone

from tickets import inventory, money, stripe_api
from tickets.models import (Order, Payout, PayoutAccount, PromoCode, Refund, Revocation, StripeEvent, Ticket,
                            TicketSettings, TicketType)

logger = logging.getLogger(__name__)


class PaymentError(Exception):
    """Shown to the person (organizer or buyer)."""


def site(path):
    return settings.SITE_URL + path


# ---------------------------------------------------------------------------
# Connect (organizer payouts)
# ---------------------------------------------------------------------------

def connect_account(user, email=''):
    """The organizer's Express account, created on first use with manual payouts."""
    existing = PayoutAccount.objects.filter(user=user).order_by('-created_at').first()
    if existing is not None:
        return existing
    data = stripe_api.post('/v1/accounts', {
        'type': 'express',
        'country': settings.STRIPE_CONNECT_COUNTRY,
        'email': email or user.email or None,
        'capabilities': {'card_payments': {'requested': True}, 'transfers': {'requested': True}},
        'business_profile': {'product_description': 'Event tickets sold through Attnd'},
        'settings': {'payouts': {'schedule': {'interval': 'manual'}}},
        'metadata': {'attnd_user_id': user.pk},
    }, idempotency_key=f'connect-account-user-{user.pk}')
    account = PayoutAccount.objects.create(user=user, stripe_account_id=data['id'], email=email or user.email or '',
                                           livemode=bool(data.get('livemode')))
    sync_account(account, data)
    return account


def onboarding_url(account, event):
    back = site(reverse('tickets_console:payouts', args=[event.pk]))
    data = stripe_api.post('/v1/account_links', {
        'account': account.stripe_account_id, 'type': 'account_onboarding',
        'refresh_url': back + '?connect=refresh', 'return_url': back + '?connect=return'})
    return data['url']


def dashboard_url(account):
    return stripe_api.post(f'/v1/accounts/{account.stripe_account_id}/login_links')['url']


def sync_account(account, data=None):
    data = data or stripe_api.get(f'/v1/accounts/{account.stripe_account_id}')
    account.charges_enabled = bool(data.get('charges_enabled'))
    account.payouts_enabled = bool(data.get('payouts_enabled'))
    account.details_submitted = bool(data.get('details_submitted'))
    account.requirements_due = list((data.get('requirements') or {}).get('currently_due') or [])[:50]
    account.updated_at = timezone.now()
    account.save()
    return account


# ---------------------------------------------------------------------------
# Checkout
# ---------------------------------------------------------------------------

def order_url(order):
    return reverse('tickets:order', args=[order.public_id, order.key])


def start_checkout(order):
    """Move a held order to Stripe Checkout; returns the hosted page's URL. Free orders never come here."""
    if order.total_cents <= 0:
        raise PaymentError('This order has nothing to pay.')
    now = timezone.now()
    if order.status == Order.PENDING and order.stripe_session_id and order.expires_at and order.expires_at > now:
        session = stripe_api.get(f'/v1/checkout/sessions/{order.stripe_session_id}')
        if session.get('status') == 'open' and session.get('url'):
            return session['url']
    if order.status not in Order.ACTIVE_HOLDS or (order.expires_at and order.expires_at <= now):
        raise PaymentError('Your hold expired. Please pick your tickets again.')
    event = order.event
    ts = inventory.ticketing(event)
    account = ts.payout_account
    if not inventory.paid_ready(event, ts):
        raise PaymentError('Paid tickets aren’t available yet.')
    expires = now + timedelta(minutes=settings.TICKETS_CHECKOUT_MINUTES)
    lines = []
    for item in order.items.select_related('ticket_type'):
        amount = item.line_cents
        if amount <= 0:
            continue
        name = item.ticket_type.name if item.quantity == 1 else f'{item.quantity} × {item.ticket_type.name}'
        lines.append({'quantity': 1, 'price_data': {
            'currency': order.currency, 'unit_amount': amount,
            'product_data': {'name': f'{name} · {event.name}'[:250],
                             **({'description': f'Includes {money.fmt(item.discount_cents, order.currency)} off'}
                                if item.discount_cents else {})}}})
    if order.fee_cents:
        lines.append({'quantity': 1, 'price_data': {
            'currency': order.currency, 'unit_amount': order.fee_cents,
            'product_data': {'name': 'Processing fee',
                             'description': 'Card processing, paid by the buyer so the organizer gets the full price'}}})
    page = site(order_url(order))
    session = stripe_api.post('/v1/checkout/sessions', {
        'mode': 'payment',
        'line_items': lines,
        'customer_email': order.email or None,
        'client_reference_id': str(order.public_id),
        'metadata': {'order_id': str(order.public_id), 'event_id': event.pk},
        'success_url': page + '?paid=1&session_id={CHECKOUT_SESSION_ID}',
        'cancel_url': page + '?cancelled=1',
        'expires_at': int(expires.timestamp()),
        'payment_intent_data': {
            'application_fee_amount': order.fee_cents,
            'transfer_data': {'destination': account.stripe_account_id},
            'on_behalf_of': account.stripe_account_id,
            'description': f'{event.name} tickets, order {order.ref}'[:1000],
            'metadata': {'order_id': str(order.public_id), 'event_id': event.pk, 'order_ref': order.ref},
        },
    }, idempotency_key=f'checkout-{order.public_id}-{int(expires.timestamp()) // 60}')
    Order.objects.filter(pk=order.pk).update(status=Order.PENDING, stripe_session_id=session['id'], expires_at=expires,
                                             stripe_account_id=account.stripe_account_id, updated_at=now)
    order.refresh_from_db()
    return session['url']


def expire_checkout(order):
    """Close an order's open Checkout Session so it can't be paid after its hold ended (best effort)."""
    if not order.stripe_session_id or not stripe_api.configured():
        return
    try:
        stripe_api.post(f'/v1/checkout/sessions/{order.stripe_session_id}/expire')
    except stripe_api.StripeError as exc:
        logger.info('Could not expire session %s: %s', order.stripe_session_id, exc)


def _issue(order):
    """Create the order's tickets (inside the caller's transaction). Fee shares add up to the order's fee."""
    amounts, types = [], []
    for item in order.items.select_related('ticket_type').order_by('id'):
        per = item.line_cents // item.quantity if item.quantity else 0
        extra = item.line_cents - per * item.quantity
        for i in range(item.quantity):
            amounts.append(per + (1 if i < extra else 0))
            types.append(item.ticket_type)
    fees = money.split_fee(order.fee_cents, amounts)
    holders = (order.answers or {}).get('_holders') or []
    tickets = []
    for i, (ticket_type, amount, fee) in enumerate(zip(types, amounts, fees)):
        holder = holders[i] if i < len(holders) and isinstance(holders[i], dict) else {}
        answers = holder.get('answers') if isinstance(holder.get('answers'), dict) else {}
        tickets.append(Ticket.objects.create(
            order=order, event=order.event, ticket_type=ticket_type, price_cents=amount, fee_cents=fee,
            holder_name=(holder.get('name') or order.name)[:200], holder_email=order.email, answers=answers))
    return tickets


def _capacity_still_free(order):
    """For an order whose hold lapsed before payment arrived: is there still room for it?"""
    now = timezone.now()
    type_ids = sorted(order.items.values_list('ticket_type_id', flat=True))
    list(TicketType.objects.select_for_update().filter(id__in=type_ids).order_by('id'))
    left = inventory.availability(order.event, now)
    needed = {}
    for item in order.items.all():
        needed[item.ticket_type_id] = needed.get(item.ticket_type_id, 0) + item.quantity
    return all(left.get(t) is None or left[t] >= n for t, n in needed.items())


def fulfil(order_id, payment_intent_id='', charge_id='', transfer_id='', paid_cents=None):
    """Payment confirmed (webhook): issue the tickets once. Safe to call any number of times."""
    late_refund = False
    with transaction.atomic():
        order = Order.objects.select_for_update().filter(public_id=order_id).select_related('event').first()
        if order is None:
            logger.warning('Payment for unknown order %s', order_id)
            return None
        if order.status in (Order.PAID, Order.REFUNDED):
            return order
        if paid_cents is not None and paid_cents < order.total_cents:
            logger.error('Order %s paid %s of %s', order.ref, paid_cents, order.total_cents)
            return order
        if order.status in (Order.EXPIRED, Order.CANCELLED) and not _capacity_still_free(order):
            late_refund = True
        order.stripe_payment_intent_id = payment_intent_id or order.stripe_payment_intent_id
        order.stripe_charge_id = charge_id or order.stripe_charge_id
        order.stripe_transfer_id = transfer_id or order.stripe_transfer_id
        if late_refund:
            order.status = Order.CANCELLED
            order.note = (order.note + '\nPaid after the hold expired and sold out: refunded automatically.').strip()
            order.save()
        else:
            order.status = Order.PAID
            order.paid_at = timezone.now()
            order.save()
            _issue(order)
            if order.promo_id:
                PromoCode.objects.filter(pk=order.promo_id).update(used_count=F('used_count') + 1)
    if late_refund:
        _refund_unissued(order)
        return order
    transaction.on_commit(lambda: _after_paid(order.pk))
    return order


def _after_paid(order_pk):
    order = Order.objects.select_related('event').get(pk=order_pk)
    from tickets import emails
    emails.send_order(order)
    inventory.refresh_sold_out(order.event)
    schedule_payout(order.event)


def complete_free(order):
    """A free / RSVP order: no Stripe, tickets right away."""
    if order.total_cents != 0:
        raise PaymentError('This order isn’t free.')
    if order.status not in Order.ACTIVE_HOLDS or (order.expires_at and order.expires_at <= timezone.now()):
        raise PaymentError('Your hold expired. Please pick your tickets again.')
    return fulfil(order.public_id)


def _charge_details(payment_intent_id):
    """(charge id, transfer id, amount received) of a PaymentIntent."""
    pi = stripe_api.get(f'/v1/payment_intents/{payment_intent_id}', {'expand': ['latest_charge']})
    charge = pi.get('latest_charge') or {}
    if isinstance(charge, str):
        return charge, '', pi.get('amount_received')
    transfer = charge.get('transfer') or ''
    return charge.get('id', ''), transfer if isinstance(transfer, str) else transfer.get('id', ''), pi.get(
        'amount_received')


def _refund_unissued(order):
    try:
        stripe_api.post('/v1/refunds', {'payment_intent': order.stripe_payment_intent_id,
                                        'reason': 'requested_by_customer',
                                        'metadata': {'order_id': str(order.public_id), 'why': 'sold_out_after_hold'}},
                        idempotency_key=f'refund-late-{order.public_id}')
        if order.stripe_transfer_id:
            stripe_api.post(f'/v1/transfers/{order.stripe_transfer_id}/reversals',
                            {'amount': order.total_cents - order.fee_cents},
                            idempotency_key=f'reversal-late-{order.public_id}')
        Order.objects.filter(pk=order.pk).update(refunded_cents=order.total_cents - order.fee_cents,
                                                 fee_refunded_cents=order.fee_cents, status=Order.REFUNDED)
    except stripe_api.StripeError:
        logger.exception('Could not refund late order %s', order.ref)
    from tickets import emails
    emails.send_sold_out_refund(order)


# ---------------------------------------------------------------------------
# Refunds
# ---------------------------------------------------------------------------

class RefundError(PaymentError):
    pass


def refund_policy(order, now=None):
    """(allowed, automatic, message) for a buyer asking to refund this order."""
    now = now or timezone.now()
    ts = inventory.ticketing(order.event)
    if order.status != Order.PAID or order.net_cents <= 0 and not order.is_free:
        return False, False, 'This order can’t be refunded.'
    if ts.cancelled:
        return True, True, 'The event was cancelled: you get everything back, fees included.'
    if now > order.event.start_time:
        return False, False, 'Refunds aren’t possible after the event has started.'
    if ts.refund_policy == TicketSettings.REFUND_NONE:
        return False, False, 'The organizer doesn’t offer refunds for this event.'
    if ts.refund_policy == TicketSettings.REFUND_UNTIL:
        deadline = order.event.start_time - timedelta(days=ts.refund_days_before)
        if now <= deadline:
            return True, True, f'Refunds are automatic until {ts.refund_days_before} days before the event.'
        return False, False, 'The refund deadline for this event has passed.'
    return True, False, 'The organizer reviews refund requests.'


def refund(order, tickets=None, amount_cents=None, include_fee=None, reason='', by=None):
    """Refund ticket money (and the fee only when the event is cancelled). Returns the Refund.

    - `tickets`: refund these tickets in full (their price; voided). Default: every valid ticket of the order.
    - `amount_cents` without tickets: a partial refund of ticket money; tickets stay valid.
    """
    ts = inventory.ticketing(order.event)
    if include_fee is None:
        include_fee = ts.cancelled
    with transaction.atomic():
        order = Order.objects.select_for_update().get(pk=order.pk)
        if order.status not in (Order.PAID, Order.REFUNDED):
            raise RefundError('Only paid orders can be refunded.')
        valid = list(order.tickets.filter(status=Ticket.VALID))
        if tickets is None and amount_cents is None:
            tickets = valid
        chosen = [t for t in (tickets or []) if t.status == Ticket.VALID and t.order_id == order.pk]
        if tickets is not None and not chosen and amount_cents is None:
            raise RefundError('Those tickets were already refunded.')
        if chosen:
            amount = sum(t.price_cents - t.refunded_cents for t in chosen)
            fee = sum(t.fee_cents for t in chosen) if include_fee else 0
        else:
            amount = int(amount_cents or 0)
            fee = 0
        amount = min(amount, order.refundable_cents)
        fee = min(fee, order.fee_cents - order.fee_refunded_cents)
        if amount + fee <= 0 and not (chosen and order.total_cents == 0):
            raise RefundError('There is nothing left to refund.')
        record = Refund.objects.create(order=order, amount_cents=amount, fee_cents=fee, reason=reason[:200],
                                       created_by=by if by and by.is_authenticated else None)
        if chosen:
            record.tickets.set(chosen)
        if amount + fee > 0:
            if not order.stripe_payment_intent_id:
                record.status, record.error = 'failed', 'No payment on file'
                record.save()
                raise RefundError('This order has no Stripe payment to refund.')
            try:
                data = stripe_api.post('/v1/refunds', {
                    'payment_intent': order.stripe_payment_intent_id, 'amount': amount + fee,
                    'reason': 'requested_by_customer',
                    'metadata': {'order_id': str(order.public_id), 'refund_id': record.pk}},
                    idempotency_key=f'refund-{record.pk}')
                record.stripe_refund_id = data.get('id', '')
                if order.stripe_transfer_id and amount > 0:
                    reversal = stripe_api.post(f'/v1/transfers/{order.stripe_transfer_id}/reversals', {
                        'amount': amount, 'metadata': {'order_id': str(order.public_id), 'refund_id': record.pk}},
                        idempotency_key=f'reversal-{record.pk}')
                    record.stripe_reversal_id = reversal.get('id', '')
            except stripe_api.StripeError as exc:
                record.status, record.error = 'failed', str(exc)[:2000]
                record.save()
                raise RefundError(f'Stripe refused the refund: {exc}')
        record.status = 'succeeded'
        record.save()
        order.refunded_cents += amount
        order.fee_refunded_cents += fee
        for t in chosen:
            t.refunded_cents = t.price_cents
            void(t, Ticket.REFUNDED, 'refund')
        if not order.tickets.filter(status=Ticket.VALID).exists():
            order.status = Order.REFUNDED
        order.save()
    transaction.on_commit(lambda: _after_refund(record.pk))
    return record


def _after_refund(refund_pk):
    record = Refund.objects.select_related('order__event').get(pk=refund_pk)
    from tickets import emails, waitlist
    emails.send_refund(record)
    waitlist.notify_if_available(record.order.event)
    inventory.refresh_sold_out(record.order.event)


def void(ticket, status=Ticket.VOID, reason='void'):
    """Stop a ticket admitting anyone: new version, revocation of every older token, Wallet passes updated."""
    revoked_version = ticket.version
    ticket.status = status
    ticket.version += 1
    ticket.save()
    Revocation.objects.create(event_id=ticket.event_id, ticket=ticket, version=revoked_version, reason=reason)
    transaction.on_commit(lambda: _wallet_changed(ticket.pk))


def _wallet_changed(ticket_pk):
    from tickets import wallet
    wallet.ticket_changed(ticket_pk)


def cancel_event(event, reason, by=None):
    """The organizer cancelled the event: stop sales, refund everyone in full (fees too), tell the holders."""
    ts = inventory.ticketing(event)
    if not ts.cancelled:
        TicketSettings.objects.filter(pk=ts.pk).update(cancelled_at=timezone.now(), on_sale=False,
                                                       cancel_reason=reason[:2000])
        event.bump_version()
        from tickets import emails
        transaction.on_commit(lambda: emails.send_cancellation(event))
    Payout.objects.filter(event=event, status=Payout.SCHEDULED).update(status=Payout.NOTHING,
                                                                       error='Event cancelled')
    return process_cancellations(event, by)


def process_cancellations(event=None, by=None, limit=200):
    """Refund the paid orders of cancelled events (resumable: the scheduled task finishes big events)."""
    qs = Order.objects.filter(status=Order.PAID, event__ticketing__cancelled_at__isnull=False)
    if event is not None:
        qs = qs.filter(event=event)
    done = failed = 0
    for order in qs.select_related('event')[:limit]:
        try:
            if order.total_cents == 0:
                with transaction.atomic():
                    for t in order.tickets.filter(status=Ticket.VALID):
                        void(t, Ticket.VOID, 'cancelled')
                    Order.objects.filter(pk=order.pk).update(status=Order.REFUNDED)
            else:
                refund(order, include_fee=True, reason='Event cancelled', by=by)
            done += 1
        except PaymentError:
            failed += 1
            logger.exception('Cancellation refund failed for order %s', order.ref)
    return done, failed


# ---------------------------------------------------------------------------
# Payouts: held until after the event
# ---------------------------------------------------------------------------

def release_at(event, ts=None):
    ts = ts or inventory.ticketing(event)
    days = ts.payout_delay_days if ts.payout_delay_days is not None else settings.TICKETS_PAYOUT_DELAY_DAYS
    return event.effective_end + timedelta(days=days)


def schedule_payout(event):
    ts = inventory.ticketing(event)
    if ts.cancelled or ts.payout_account_id is None:
        return None
    payout = Payout.objects.filter(event=event, status=Payout.SCHEDULED).first()
    when = release_at(event, ts)
    if payout is None:
        return Payout.objects.create(event=event, account=ts.payout_account, currency=ts.currency, release_at=when)
    if payout.release_at != when or payout.account_id != ts.payout_account_id:
        payout.release_at, payout.account = when, ts.payout_account
        payout.save(update_fields=['release_at', 'account'])
    return payout


def event_net_cents(event):
    """Ticket money the organizer keeps for the event, minus what was already paid out."""
    orders = Order.objects.filter(event=event, status__in=[Order.PAID, Order.REFUNDED])
    net = sum(o.net_cents for o in orders if not o.dispute_status or o.dispute_status == 'won')
    paid_out = sum(Payout.objects.filter(event=event, status=Payout.RELEASED).values_list('amount_cents', flat=True))
    return max(0, net - paid_out)


def release_due(now=None):
    """Pay out every event whose hold period ended (scheduled task). Returns the number released."""
    now = now or timezone.now()
    released = 0
    for payout in Payout.objects.filter(status=Payout.SCHEDULED, release_at__lte=now).select_related('event',
                                                                                                    'account'):
        event = payout.event
        if event.effective_end > now:  # the event moved later
            schedule_payout(event)
            continue
        amount = event_net_cents(event)
        if amount <= 0:
            payout.status, payout.released_at = Payout.NOTHING, now
            payout.save()
            continue
        if payout.account is None:
            payout.error = 'No payout account'
            payout.save(update_fields=['error'])
            continue
        try:
            data = stripe_api.post('/v1/payouts', {
                'amount': amount, 'currency': payout.currency,
                'description': f'Attnd tickets: {event.name}'[:200],
                'statement_descriptor': 'ATTND TICKETS',
                'metadata': {'event_id': event.pk, 'payout_id': payout.pk}},
                account=payout.account.stripe_account_id, idempotency_key=f'payout-{payout.pk}')
        except stripe_api.StripeError as exc:
            payout.error = str(exc)[:2000]
            # Funds still settling: try again on the next run. Anything else needs a person.
            if exc.code not in ('balance_insufficient', 'insufficient_funds'):
                payout.status = Payout.FAILED
            payout.save()
            continue
        payout.amount_cents, payout.status, payout.released_at = amount, Payout.RELEASED, now
        payout.stripe_payout_id, payout.stripe_status, payout.error = data.get('id', ''), data.get('status', ''), ''
        payout.save()
        released += 1
    return released


# ---------------------------------------------------------------------------
# Webhooks
# ---------------------------------------------------------------------------

def _order_for(obj):
    meta = obj.get('metadata') or {}
    order_id = meta.get('order_id') or obj.get('client_reference_id')
    return Order.objects.filter(public_id=order_id).first() if order_id else None


def handle_event(evt):
    """Process one verified webhook event. Idempotent; raises on errors so Stripe retries."""
    kind = evt.get('type', '')
    obj = (evt.get('data') or {}).get('object') or {}
    if kind in ('checkout.session.completed', 'checkout.session.async_payment_succeeded'):
        if obj.get('payment_status') in ('paid', 'no_payment_required'):
            order = _order_for(obj)
            if order is None:
                return 'unknown order'
            pi = obj.get('payment_intent') or ''
            pi = pi if isinstance(pi, str) else pi.get('id', '')
            charge, transfer, received = _charge_details(pi) if pi else ('', '', None)
            fulfil(order.public_id, pi, charge, transfer, paid_cents=obj.get('amount_total'))
            return 'fulfilled'
        return 'awaiting payment'
    if kind == 'payment_intent.succeeded':
        order = _order_for(obj)
        if order is None:
            return 'not ours'
        charge = obj.get('latest_charge') or ''
        transfer = ''
        if isinstance(charge, dict):
            transfer = charge.get('transfer') or ''
            charge = charge.get('id', '')
        if not transfer:
            charge, transfer, _ = _charge_details(obj['id'])
        fulfil(order.public_id, obj['id'], charge, transfer, paid_cents=obj.get('amount_received'))
        return 'fulfilled'
    if kind in ('checkout.session.expired', 'checkout.session.async_payment_failed'):
        order = _order_for(obj)
        if order is not None:
            Order.objects.filter(pk=order.pk, status=Order.PENDING).update(status=Order.EXPIRED,
                                                                          updated_at=timezone.now())
            from tickets import waitlist
            waitlist.notify_if_available(order.event)
        return 'expired'
    if kind == 'payment_intent.payment_failed':
        return 'noted'
    if kind == 'charge.refunded':
        return _reconcile_refund(obj)
    if kind.startswith('charge.dispute.'):
        return _dispute(kind, obj)
    if kind == 'account.updated':
        account = PayoutAccount.objects.filter(stripe_account_id=obj.get('id')).first()
        if account is not None:
            sync_account(account, obj)
        return 'synced'
    if kind.startswith('payout.'):
        Payout.objects.filter(stripe_payout_id=obj.get('id')).update(stripe_status=obj.get('status', '')[:32])
        if kind == 'payout.failed':
            Payout.objects.filter(stripe_payout_id=obj.get('id')).update(
                status=Payout.FAILED, error=(obj.get('failure_message') or 'Payout failed')[:2000])
        return 'payout'
    return 'ignored'


def _reconcile_refund(charge):
    """A refund made in the Stripe Dashboard (not through Attnd): record it; a full refund voids the tickets."""
    order = Order.objects.filter(stripe_charge_id=charge.get('id')).first() or _order_for(charge)
    if order is None:
        return 'not ours'
    refunded = int(charge.get('amount_refunded') or 0)
    known = order.refunded_cents + order.fee_refunded_cents
    if refunded <= known:
        return 'known'
    with transaction.atomic():
        order = Order.objects.select_for_update().get(pk=order.pk)
        extra = refunded - (order.refunded_cents + order.fee_refunded_cents)
        if extra <= 0:
            return 'known'
        ticket_part = min(extra, order.refundable_cents)
        order.refunded_cents += ticket_part
        order.fee_refunded_cents += extra - ticket_part
        Refund.objects.create(order=order, amount_cents=ticket_part, fee_cents=extra - ticket_part,
                              reason='Refunded in the Stripe Dashboard', status='succeeded')
        if charge.get('refunded'):
            for t in order.tickets.filter(status=Ticket.VALID):
                void(t, Ticket.REFUNDED, 'refund')
            order.status = Order.REFUNDED
        order.save()
    return 'recorded'


def _dispute(kind, dispute):
    order = Order.objects.filter(stripe_charge_id=dispute.get('charge')).first()
    if order is None and dispute.get('payment_intent'):
        order = Order.objects.filter(stripe_payment_intent_id=dispute['payment_intent']).first()
    if order is None:
        return 'not ours'
    status = (dispute.get('status') or kind.rsplit('.', 1)[-1])[:32]
    Order.objects.filter(pk=order.pk).update(dispute_status=status, updated_at=timezone.now())
    if kind == 'charge.dispute.created':
        from tickets import emails
        emails.send_dispute_notice(order)
    if kind == 'charge.dispute.closed' and status == 'lost':
        with transaction.atomic():
            for t in order.tickets.filter(status=Ticket.VALID):
                void(t, Ticket.VOID, 'dispute')
    return status


def process_webhook(evt, account=''):
    """Store-and-process with idempotency. Returns (http status, text)."""
    if bool(evt.get('livemode')) != stripe_api.livemode():
        return 400, 'livemode mismatch'  # production never takes test events (and dev never takes live ones)
    record, created = StripeEvent.objects.get_or_create(
        id=evt.get('id', '')[:128], defaults={'type': evt.get('type', '')[:64], 'livemode': bool(evt.get('livemode')),
                                              'account': (evt.get('account') or account or '')[:64]})
    if record.processed_at is not None:
        return 200, 'duplicate'
    try:
        result = handle_event(evt)
    except Exception as exc:
        logger.exception('Stripe webhook %s (%s) failed', record.id, record.type)
        StripeEvent.objects.filter(pk=record.pk).update(error=str(exc)[:2000])
        return 500, 'error'
    StripeEvent.objects.filter(pk=record.pk).update(processed_at=timezone.now(), error='')
    return 200, result or 'ok'
