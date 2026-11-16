"""Door check-in: what a door device downloads to work offline, and merging the scans it uploads later.

Offline model (spec: offline door scanning is critical): before doors open a device downloads the event's public
keys, ticket list and revocations. During the event it needs no server: it verifies the QR's Ed25519 signature,
looks the ticket up in its list (status, version, already in?), applies newer revocations it fetched, and queues
every scan. When it is online again it uploads the queue in batches; the server merges scans from every gate:
the earliest admission wins, later ones are flagged duplicates. Uploads are idempotent by the device's scan id.

Minimal personal data reaches door devices: the holder's name (for manual search and to greet them) and a hash of
their email (`email_hash` = SHA-256 of "<event id>:<lower-case email>", so staff can search by email without the
list containing addresses). No phone numbers, prices or answers.
"""

import hashlib
from datetime import timedelta
from zoneinfo import ZoneInfo

from django.db import IntegrityError, transaction
from django.db.models import Count, Q
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from events.models import Event, EventMember
from tickets import signing
from tickets.models import CheckIn, DoorStaff, Revocation, Ticket, TicketType, normalize_email

MAX_BATCH = 500


def email_hash(event_id, email):
    return hashlib.sha256(f'{event_id}:{normalize_email(email)}'.encode()).hexdigest()


def door_events(email):
    """Ticketed events this email may scan for: door staff, or the event's team (owner, admins, contributors)."""
    from django.contrib.auth.models import User
    email = normalize_email(email)
    ids = set(DoorStaff.objects.filter(email=email).values_list('event_id', flat=True))
    user = User.objects.filter(email__iexact=email, is_active=True).first() if email else None
    if user is not None:
        ids |= set(Event.objects.filter(user=user).values_list('id', flat=True))
        ids |= set(EventMember.objects.filter(user=user).values_list('event_id', flat=True))
    return (Event.objects.filter(id__in=ids, ticketing__enabled=True, plan=Event.PLAN_STUDIO)
            .exclude(status=Event.STATUS_DRAFT).order_by('start_time'))


def can_work_door(event, email):
    return door_events(email).filter(pk=event.pk).exists()


def _tz(event):
    try:
        return ZoneInfo(event.timezone or 'UTC')
    except Exception:
        return ZoneInfo('UTC')


def ticket_row(ticket):
    return {
        'id': ticket.id, 'code': ticket.code, 'type_id': ticket.ticket_type_id, 'status': ticket.status,
        'version': ticket.version, 'name': ticket.holder_name, 'email_hash': email_hash(ticket.event_id,
                                                                                         ticket.holder_email),
        'order_ref': ticket.order.ref, 'checked_in_at': ticket.checked_in_at.isoformat() if ticket.checked_in_at else None,
        'checked_in_gate': ticket.checked_in_gate or None, 'inside': ticket.inside,
    }


def manifest(event):
    """Everything a door device needs to check tickets offline."""
    now = timezone.now()
    types = TicketType.objects.filter(event=event).order_by('sort_order', 'id')
    tickets = Ticket.objects.filter(event=event).select_related('order').order_by('id')
    last = Revocation.objects.filter(event=event).order_by('-id').values_list('id', flat=True).first() or 0
    return {
        'schema': 1,
        'generated_at': now.isoformat(),
        'event': {'id': event.id, 'name': event.name, 'event_code': event.event_code, 'timezone': event.timezone,
                  'start_time': event.start_time.isoformat(),
                  'end_time': event.end_time.isoformat() if event.end_time else None},
        'keys': signing.public_keys(event),
        'ticket_types': [{'id': t.id, 'name': t.name, 'reentry': t.reentry, 'add_on': t.add_on,
                          'valid_on': t.valid_on.isoformat() if t.valid_on else None} for t in types],
        'tickets': [ticket_row(t) for t in tickets],
        'revocations_cursor': last,
        'gates': sorted({g for g in CheckIn.objects.filter(event=event).exclude(gate='').values_list('gate', flat=True)}),
    }


def revocations(event, since=0, limit=5000):
    rows = list(Revocation.objects.filter(event=event, id__gt=since).order_by('id')[:limit])
    return {
        'revocations': [{'cursor': r.id, 'ticket_id': r.ticket_id, 'version': r.version, 'reason': r.reason,
                         'at': r.created_at.isoformat()} for r in rows],
        'cursor': rows[-1].id if rows else since,
        'more': len(rows) == limit,
    }


def _when(value, now):
    at = parse_datetime(value) if isinstance(value, str) else None
    if at is None or timezone.is_naive(at):
        return now
    return min(at, now + timedelta(minutes=5))  # a device clock far ahead can't book the future


def _evaluate(event, ticket, kind, at, token_version):
    """(result, earlier OK check-in or None). Called with the ticket row locked."""
    if ticket is None or ticket.event_id != event.id:
        return CheckIn.INVALID, None
    if ticket.status != Ticket.VALID:
        return CheckIn.VOID, None
    if token_version is not None and token_version < ticket.version:
        return CheckIn.REVOKED, None
    valid_on = ticket.ticket_type.valid_on
    if valid_on and timezone.localtime(at, _tz(event)).date() != valid_on:
        return CheckIn.WRONG_DAY, None
    if kind == CheckIn.OUT:
        return CheckIn.OK, None
    first = (CheckIn.objects.filter(ticket=ticket, kind=CheckIn.IN, result=CheckIn.OK).order_by('at', 'id').first())
    if first is None:
        return CheckIn.OK, None
    if ticket.ticket_type.reentry:
        last = CheckIn.objects.filter(ticket=ticket, result=CheckIn.OK, at__lte=at).order_by('-at', '-id').first()
        if last is None or last.kind == CheckIn.OUT:
            return CheckIn.OK, None
    return CheckIn.DUPLICATE, first


def _apply(ticket, checkin):
    """Update the ticket's check-in state from OK scans (earliest admission wins)."""
    oks = list(CheckIn.objects.filter(ticket=ticket, result=CheckIn.OK).order_by('at', 'id'))
    first_in = next((c for c in oks if c.kind == CheckIn.IN), None)
    ticket.checked_in_at = first_in.at if first_in else None
    ticket.checked_in_gate = first_in.gate if first_in else ''
    ticket.checked_in_device = first_in.device_id if first_in else ''
    ticket.inside = bool(oks) and oks[-1].kind == CheckIn.IN
    ticket.save(update_fields=['checked_in_at', 'checked_in_gate', 'checked_in_device', 'inside', 'updated_at'])


def result_row(c):
    row = {'client_id': c.client_id, 'result': c.result, 'ticket_id': c.ticket_id, 'flagged': c.flagged,
           'at': c.at.isoformat()}
    if c.ticket_id and c.result == CheckIn.DUPLICATE and c.ticket and c.ticket.checked_in_at:
        row['first_at'] = c.ticket.checked_in_at.isoformat()
        row['first_gate'] = c.ticket.checked_in_gate or None
    return row


def record(event, scan, device_id='', gate='', staff_email='', now=None):
    """Merge one uploaded scan. Idempotent by (event, client_id)."""
    now = now or timezone.now()
    client_id = str(scan.get('client_id') or scan.get('id') or '')[:64]
    if not client_id:
        return {'client_id': None, 'result': 'rejected', 'error': 'client_id is required'}
    existing = CheckIn.objects.filter(event=event, client_id=client_id).select_related('ticket').first()
    if existing is not None:
        return result_row(existing)
    kind = CheckIn.OUT if scan.get('kind') == CheckIn.OUT else CheckIn.IN
    at = _when(scan.get('at'), now)
    token_version = None
    ticket_id = None
    if scan.get('token'):
        payload = signing.verify_for_event(str(scan['token']), event)
        if payload is not None:
            ticket_id, token_version = payload['t'], payload['n']
    elif scan.get('ticket_id') is not None:
        try:
            ticket_id = int(scan['ticket_id'])
        except (TypeError, ValueError):
            ticket_id = None
    device_result = str(scan.get('device_result') or '')[:12]
    with transaction.atomic():
        ticket = (Ticket.objects.select_for_update().select_related('ticket_type').filter(pk=ticket_id, event=event)
                  .first() if ticket_id else None)
        result, first = _evaluate(event, ticket, kind, at, token_version)
        replaced = None
        if result == CheckIn.DUPLICATE and first is not None and at < first.at:
            # This offline scan happened before the one we had: it is the admission, the other the duplicate.
            replaced, result = first, CheckIn.OK
        try:
            with transaction.atomic():
                checkin = CheckIn.objects.create(
                    event=event, ticket=ticket, client_id=client_id, device_id=str(device_id)[:128],
                    gate=str(scan.get('gate') or gate or '')[:64], kind=kind, at=at, token_version=token_version,
                    result=result, device_result=device_result, staff_email=normalize_email(staff_email)[:254],
                    flagged=result == CheckIn.DUPLICATE or (device_result == CheckIn.OK and result != CheckIn.OK))
        except IntegrityError:  # the same scan arrived twice at once
            return result_row(CheckIn.objects.select_related('ticket').get(event=event, client_id=client_id))
        if replaced is not None:
            CheckIn.objects.filter(pk=replaced.pk).update(result=CheckIn.DUPLICATE, flagged=True)
        if ticket is not None and result == CheckIn.OK:
            _apply(ticket, checkin)
    checkin.ticket = ticket
    return result_row(checkin)


def sync(event, scans, device_id='', gate='', staff_email=''):
    return [record(event, s if isinstance(s, dict) else {}, device_id, gate, staff_email) for s in scans[:MAX_BATCH]]


def counts(event):
    valid = Ticket.objects.filter(event=event, status=Ticket.VALID)
    by_type = (valid.values('ticket_type_id', 'ticket_type__name').annotate(
        total=Count('id'), checked_in=Count('id', filter=Q(checked_in_at__isnull=False)))
        .order_by('ticket_type__sort_order', 'ticket_type_id'))
    by_gate = (CheckIn.objects.filter(event=event, result=CheckIn.OK, kind=CheckIn.IN).values('gate')
               .annotate(n=Count('id')).order_by('gate'))
    return {
        'total': valid.count(),
        'checked_in': valid.filter(checked_in_at__isnull=False).count(),
        'inside': valid.filter(inside=True).count(),
        'flagged': CheckIn.objects.filter(event=event, flagged=True).count(),
        'by_type': [{'type_id': r['ticket_type_id'], 'name': r['ticket_type__name'], 'total': r['total'],
                     'checked_in': r['checked_in']} for r in by_type],
        'by_gate': [{'gate': r['gate'] or None, 'checked_in': r['n']} for r in by_gate],
        'updated_at': timezone.now().isoformat(),
    }
