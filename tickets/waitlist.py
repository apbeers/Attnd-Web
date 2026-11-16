"""Waitlists for sold-out ticket types: when tickets free up (expired holds, refunds), the next people are emailed."""

from django.utils import timezone

from tickets import inventory
from tickets.models import WaitlistEntry, normalize_email


def join(ticket_type, email, name=''):
    entry, created = WaitlistEntry.objects.get_or_create(
        ticket_type=ticket_type, email=normalize_email(email),
        defaults={'event_id': ticket_type.event_id, 'name': name[:200]})
    return entry, created


def notify_if_available(event):
    """Email as many waiting people as there are tickets free per type (once each). Returns how many were told."""
    waiting = WaitlistEntry.objects.filter(event=event, notified_at__isnull=True)
    if not waiting.exists():
        return 0
    if inventory.sales_state(event)[0] != 'open':
        return 0
    left = inventory.availability(event)
    told = 0
    from tickets import emails
    for type_id in set(waiting.values_list('ticket_type_id', flat=True)):
        free = left.get(type_id)
        count = 50 if free is None else free
        if count <= 0:
            continue
        for entry in waiting.filter(ticket_type_id=type_id).select_related('ticket_type')[:count]:
            emails.send_waitlist(entry)
            WaitlistEntry.objects.filter(pk=entry.pk).update(notified_at=timezone.now())
            told += 1
    return told
