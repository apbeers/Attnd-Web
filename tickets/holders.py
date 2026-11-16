"""What ticket holders can do themselves: transfer a ticket, resend tickets, ask for a refund; plus comps."""

from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from tickets import inventory, payments
from tickets.models import (Order, OrderItem, RefundRequest, Revocation, Ticket, TicketTransfer, normalize_email,
                            secret_key)


class HolderError(Exception):
    pass


def transfer(ticket, to_email, to_name='', from_name=''):
    """Give a ticket to someone else: new QR (the old one is revoked), new private link, their own email."""
    from tickets.buyers import valid_email
    to_email = normalize_email(to_email)
    if not valid_email(to_email):
        raise HolderError('Enter a valid email address.')
    with transaction.atomic():
        ticket = Ticket.objects.select_for_update().select_related('ticket_type', 'event').get(pk=ticket.pk)
        if ticket.status != Ticket.VALID:
            raise HolderError('This ticket is no longer valid.')
        if not ticket.ticket_type.transferable:
            raise HolderError('This ticket type can’t be transferred.')
        if ticket.checked_in_at:
            raise HolderError('This ticket was already used at the door.')
        if timezone.now() > ticket.event.effective_end:
            raise HolderError('The event is over.')
        if to_email == ticket.holder_email:
            raise HolderError('That’s already the ticket holder’s email.')
        TicketTransfer.objects.create(ticket=ticket, from_email=ticket.holder_email, to_email=to_email,
                                      to_name=to_name[:200])
        Revocation.objects.create(event_id=ticket.event_id, ticket=ticket, version=ticket.version, reason='transfer')
        from tickets import wallet
        wallet.retire(ticket)
        ticket.version += 1
        ticket.holder_email = to_email
        ticket.holder_name = to_name[:200]
        ticket.key = secret_key()
        ticket.transfer_count += 1
        ticket.save()
    from tickets import emails
    transaction.on_commit(lambda: (emails.send_ticket(ticket, from_name), wallet.ticket_changed(ticket.pk)))
    return ticket


def resend(order):
    from tickets import emails
    if order.status != Order.PAID:
        raise HolderError('This order has no tickets to send.')
    return emails.send_order(order)


def request_refund(order, tickets=None, reason=''):
    """Per the event's refund policy: automatic (returns the Refund) or a request for the organizer."""
    allowed, automatic, message = payments.refund_policy(order)
    if not allowed:
        raise HolderError(message)
    if automatic:
        return payments.refund(order, tickets=tickets, reason='Requested by the buyer')
    if order.refund_requests.filter(status=RefundRequest.PENDING).exists():
        raise HolderError('You already asked; the organizer will get back to you.')
    req = RefundRequest.objects.create(order=order, reason=reason[:2000])
    if tickets:
        req.tickets.set(tickets)
    from tickets import emails
    emails.send_refund_request(req)
    return req


def comp(event, ticket_type, quantity, email, name='', by=None):
    """Free tickets from the organizer (guest list, press, sponsors): no Stripe, counted against capacity."""
    quantity = int(quantity)
    if quantity < 1 or quantity > 100:
        raise HolderError('Give between 1 and 100 tickets.')
    with transaction.atomic():
        from tickets.models import TicketType
        TicketType.objects.select_for_update().get(pk=ticket_type.pk)
        left = inventory.availability(event).get(ticket_type.id)
        if left is not None and quantity > left:
            raise HolderError(f'Only {left} left of {ticket_type.name}.')
        order = Order.objects.create(event=event, email=email, name=name, status=Order.HELD, comp=True,
                                     source='comp', currency=inventory.ticketing(event).currency,
                                     expires_at=timezone.now() + timedelta(minutes=5),
                                     note=f'Comp by {by.email if by else "organizer"}')
        OrderItem.objects.create(order=order, ticket_type=ticket_type, quantity=quantity, unit_price_cents=0)
    payments.fulfil(order.public_id)
    order.refresh_from_db()
    return order
