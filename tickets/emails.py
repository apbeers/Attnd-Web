"""Ticket emails (Mailgun through Django's mailer): codes, orders with Wallet buttons, transfers, refunds, notices.

Everything goes through `_send`, which never mails the test domain (settings.TICKETS_TEST_EMAIL_DOMAIN) and never
raises (a mail problem must not break a payment webhook). EMAIL_ALLOWED_RECIPIENTS still applies on dev.
"""

import logging
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from tickets import money

logger = logging.getLogger(__name__)


def absolute(path):
    return settings.SITE_URL + path


def ticket_links(ticket):
    from tickets import wallet
    links = {'page': absolute(reverse('tickets:ticket', args=[ticket.public_id, ticket.key]))}
    if wallet.apple_enabled():
        links['apple'] = absolute(reverse('tickets:apple_pass', args=[ticket.public_id, ticket.key]))
    if wallet.google_enabled():
        links['google'] = absolute(reverse('tickets:google_pass', args=[ticket.public_id, ticket.key]))
    return links


def when(event):
    try:
        tz = ZoneInfo(event.timezone or 'UTC')
    except Exception:
        tz = ZoneInfo('UTC')
    start = timezone.localtime(event.start_time, tz)
    return start.strftime('%A, %B %-d, %Y at %-I:%M %p')


def _send(to, subject, template, context, reply_to=None):
    from tickets.buyers import is_test_address
    recipients = [a for a in ([to] if isinstance(to, str) else to) if a and not is_test_address(a)]
    if not recipients:
        return 0
    context = {'site_url': settings.SITE_URL, 'dev': settings.TICKETS_DEV_TOOLS, **context}
    try:
        html = render_to_string(f'tickets/email/{template}.html', context)
        text = render_to_string(f'tickets/email/{template}.txt', context)
        message = EmailMultiAlternatives(subject=subject, body=text, to=recipients, reply_to=reply_to)
        message.attach_alternative(html, 'text/html')
        return message.send()
    except Exception:
        logger.exception('Could not send the %s email', template)
        return 0


def _organizer_emails(event):
    from events.models import EventMember
    emails = {event.user.email} if event.user_id and event.user.email else set()
    emails |= set(EventMember.objects.filter(event=event, role=EventMember.ROLE_ADMIN).exclude(user__email='')
                  .values_list('user__email', flat=True))
    return sorted(emails)


def send_code(email, code, purpose):
    what = 'sign in to door mode' if purpose == 'door' else 'see your tickets'
    return _send(email, f'Your Attnd code: {code}', 'code', {'code': code, 'what': what})


def send_order(order):
    event = order.event
    tickets = list(order.tickets.select_related('ticket_type'))
    ts = getattr(event, 'ticketing', None)
    return _send(order.email, f'Your tickets for {event.name}', 'order', {
        'order': order, 'event': event, 'when': when(event), 'ts': ts,
        'tickets': [{'ticket': t, 'links': ticket_links(t)} for t in tickets],
        'order_url': absolute(reverse('tickets:order', args=[order.public_id, order.key])),
        'total': money.fmt(order.total_cents, order.currency), 'fee': money.fmt(order.fee_cents, order.currency),
        'reply_to': _organizer_emails(event)[:1]}, reply_to=_organizer_emails(event)[:1] or None)


def send_ticket(ticket, from_name=''):
    """A ticket transferred to someone (their own link; the old QR code no longer works)."""
    event = ticket.event
    return _send(ticket.holder_email, f'{from_name or "Someone"} sent you a ticket for {event.name}', 'transfer', {
        'ticket': ticket, 'event': event, 'when': when(event), 'links': ticket_links(ticket), 'from_name': from_name})


def send_refund(refund):
    order = refund.order
    return _send(order.email, f'Refund for your {order.event.name} tickets', 'refund', {
        'order': order, 'event': order.event, 'refund': refund,
        'amount': money.fmt(refund.amount_cents + refund.fee_cents, order.currency)})


def send_sold_out_refund(order):
    return _send(order.email, f'Your {order.event.name} order was refunded', 'sold_out_refund',
                 {'order': order, 'event': order.event, 'amount': money.fmt(order.total_cents, order.currency)})


def send_cancellation(event):
    from tickets.models import Ticket
    emails = set(Ticket.objects.filter(event=event).values_list('holder_email', flat=True))
    emails |= set(event.orders.filter(status__in=['paid', 'refunded']).values_list('email', flat=True))
    ts = event.ticketing
    for email in sorted(e for e in emails if e):
        _send(email, f'{event.name} is cancelled', 'cancelled', {'event': event, 'reason': ts.cancel_reason})
    return len(emails)


def send_waitlist(entry):
    event = entry.event
    return _send(entry.email, f'Tickets are available for {event.name}', 'waitlist', {
        'entry': entry, 'event': event, 'url': absolute(reverse('tickets:page', args=[event.event_code]))})


def send_dispute_notice(order):
    to = _organizer_emails(order.event) + [a for _, a in settings.ADMINS]
    return _send(to, f'A payment was disputed: order {order.ref}', 'dispute', {'order': order, 'event': order.event})


def send_refund_request(request_obj):
    order = request_obj.order
    return _send(_organizer_emails(order.event), f'Refund request for order {order.ref}', 'refund_request', {
        'req': request_obj, 'order': order, 'event': order.event,
        'url': absolute(reverse('tickets_console:order', args=[order.event_id, order.pk]))})


def send_door_invite(staff):
    event = staff.event
    return _send(staff.email, f'You’re on the door team for {event.name}', 'door_invite',
                 {'staff': staff, 'event': event, 'when': when(event)})


def send_holder_message(event, email, subject, body):
    return _send(email, subject, 'holder_message', {'event': event, 'body': body},
                 reply_to=_organizer_emails(event)[:1] or None)
