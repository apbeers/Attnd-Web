"""Organizer notifications (stage I16): an email when an AI import finishes, when website changes wait for review,
and when a scheduled notification went out. Each person turns each one on or off in Account
(`ConsolePreference.notify`); all on by default. The console's service worker shows the same for background tasks
as browser notifications while a console tab is open (console/static/console/sw.js)."""

import logging

from django.urls import reverse

logger = logging.getLogger(__name__)

KINDS = {
    'import_done': 'An AI import finished',
    'review_waiting': 'Website changes are waiting for review',
    'notification_sent': 'A scheduled notification went out',
}


def wants(user, kind):
    from console.models import ConsolePreference
    pref = ConsolePreference.objects.filter(user=user).values_list('notify', flat=True).first()
    return (pref or {}).get(kind, True)


def recipients(event, kind, extra=()):
    """The owner and the event's admins (plus `extra` people) who want this kind, by email."""
    from events.models import EventMember
    people = [event.user] if event.user_id else []
    people += [m.user for m in event.members.filter(role=EventMember.ROLE_ADMIN).select_related('user')]
    people += [u for u in extra if u is not None]
    seen, out = set(), []
    for user in people:
        if user.pk in seen or not user.email or not wants(user, kind):
            continue
        seen.add(user.pk)
        out.append(user.email)
    return out


def send(event, kind, subject, lines, path, extra=()):
    from django.core.mail import send_mail

    from console.mail import email_enabled, site_url
    to = recipients(event, kind, extra)
    if not to or not email_enabled():
        return 0
    account = site_url(reverse('console:account'))
    body = '\n'.join(lines) + f'\n\nOpen it: {site_url(path)}\n\n—\nTurn these emails off in Account: {account}\n'
    try:
        send_mail(subject=subject, message=body, from_email=None, recipient_list=sorted(to))
    except Exception:
        logger.exception('Organizer email (%s) failed', kind)
        return 0
    return len(to)


def import_done(job, event):
    return send(event, 'import_done', f'{event.name} is ready in Attnd',
                [f'The AI import of “{event.name}” finished: {event.location_set.count()} locations and '
                 f'{event.scheduleditem_set.count()} schedule items. Check it, then publish it.'],
                reverse('console:event', args=[event.pk]), extra=[job.created_by])


def review_waiting(event, changeset, pending):
    return send(event, 'review_waiting', f'{pending} suggested update(s) for {event.name}',
                [f'Attnd checked your event sources and found: {changeset.summary}.'],
                reverse('console:changeset', args=[changeset.pk]))


def notification_sent(notification):
    event = notification.event
    return send(event, 'notification_sent', f'Sent: “{notification.title}” ({event.name})',
                [f'Your scheduled notification “{notification.title}” went out to '
                 f'{notification.delivered_count} device(s).', '', notification.body],
                reverse('console:notifications', args=[event.pk]))
