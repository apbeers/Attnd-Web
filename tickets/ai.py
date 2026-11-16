"""AI drafts of ticket types, descriptions, the refund policy and FAQ, reviewed like every AI change.

The organizer describes what they want; the model returns structured JSON; we turn it into a ChangeSet of Changes
(entities `ticket_type` and `ticket_settings`) that the existing red/green review shows. Money fields (kind, price,
quantity) are listed in `Change.human_fields`: unticked in the review and never applied automatically, so a person
approves every one. Applying snapshots an EventRevision first (History can roll back). Sold tickets never change:
quantities can't go below what is sold, and types are never deleted.
"""

import json
import logging

from django.conf import settings
from django.utils import timezone

from scrape import diff
from scrape.models import Change, IngestJob
from tickets import inventory, money
from tickets.models import TicketType

logger = logging.getLogger(__name__)

TYPE_FIELDS = ['name', 'description', 'kind', 'price_cents', 'quantity', 'max_per_order', 'add_on', 'reentry',
               'transferable']
SETTINGS_FIELDS = ['page_text', 'refund_policy_text', 'faq']

SCHEMA = {
    'type': 'object', 'additionalProperties': False,
    'required': ['ticket_types', 'page_text', 'refund_policy_text', 'faq', 'notes'],
    'properties': {
        'ticket_types': {'type': 'array', 'items': {
            'type': 'object', 'additionalProperties': False,
            'required': ['name', 'description', 'kind', 'price_dollars', 'quantity', 'max_per_order', 'add_on',
                         'reentry', 'transferable'],
            'properties': {
                'name': {'type': 'string'}, 'description': {'type': 'string'},
                'kind': {'type': 'string', 'enum': ['paid', 'free', 'donation']},
                'price_dollars': {'type': 'number'}, 'quantity': {'type': ['integer', 'null']},
                'max_per_order': {'type': 'integer'}, 'add_on': {'type': 'boolean'}, 'reentry': {'type': 'boolean'},
                'transferable': {'type': 'boolean'}}}},
        'page_text': {'type': 'string'}, 'refund_policy_text': {'type': 'string'}, 'faq': {'type': 'string'},
        'notes': {'type': 'string'},
    },
}

PROMPT = """You set up ticketing for an event on Attnd. Draft the ticket types and short buyer-facing texts the
organizer asks for. Rules:
- Prices are in US dollars BEFORE the processing fee (buyers pay the fee on top; never add it yourself).
- kind "free" for free/RSVP tickets (price 0), "donation" for pay-what-you-want (price = the minimum).
- quantity null = no limit. add_on true only for extras that need another ticket (parking, merch).
- Keep existing ticket types unless asked: return them with the same name (change only what was asked).
- Descriptions: 1-2 plain sentences, what the ticket includes. No prices in the text.
- page_text: 1-3 sentences for the top of the ticket page. refund_policy_text: plain, matching what the organizer
  said (if nothing was said, keep the current text or write "Refunds are available until 7 days before the event.").
  faq: a few short Q&A lines (doors, ID, re-entry, accessibility) only from facts you were given.
- Never invent facts (performers, venues, times). Put doubts in notes."""


def _call_model(messages):
    from openai import OpenAI
    response = OpenAI().chat.completions.create(
        model=settings.OPENAI_MODEL, messages=messages,
        response_format={'type': 'json_schema', 'json_schema': {'name': 'ticket_draft', 'strict': True,
                                                                'schema': SCHEMA}})
    return json.loads(response.choices[0].message.content)


def _context(event):
    ts = inventory.ticketing(event)
    current = [{'name': t.name, 'description': t.description, 'kind': t.kind, 'price_dollars': t.price_cents / 100,
                'quantity': t.quantity, 'max_per_order': t.max_per_order, 'add_on': t.add_on, 'reentry': t.reentry,
                'transferable': t.transferable} for t in TicketType.objects.filter(event=event, active=True)]
    return {'event': {'name': event.name, 'description': event.description, 'start': event.start_time.isoformat(),
                      'end': event.end_time.isoformat() if event.end_time else None, 'address': event.address,
                      'timezone': event.timezone},
            'current_ticket_types': current,
            'current_texts': {f: getattr(ts, f) for f in SETTINGS_FIELDS}}


def start(event, instructions, user):
    from scrape import jobs
    job = IngestJob.objects.create(kind=IngestJob.KIND_TICKETS, event=event, created_by=user,
                                   instructions=instructions[:2000], trigger=IngestJob.TRIGGER_CONSOLE)
    jobs.enqueue(job)
    return job


def _proposed_type(raw):
    kind = raw.get('kind') if raw.get('kind') in dict(TicketType.KIND_CHOICES) else TicketType.KIND_PAID
    try:
        price = 0 if kind == TicketType.KIND_FREE else max(0, round(float(raw.get('price_dollars') or 0) * 100))
    except (TypeError, ValueError):
        price = 0
    quantity = raw.get('quantity')
    return {
        'name': str(raw.get('name') or '').strip()[:120], 'description': str(raw.get('description') or '')[:2000],
        'kind': kind, 'price_cents': price,
        'quantity': int(quantity) if isinstance(quantity, int) and quantity >= 0 else None,
        'max_per_order': max(1, min(int(raw.get('max_per_order') or 10), settings.TICKETS_MAX_PER_ORDER)),
        'add_on': bool(raw.get('add_on')), 'reentry': bool(raw.get('reentry')),
        'transferable': raw.get('transferable', True) is not False,
    }


def compute_changes(event, data):
    """The model's draft → change dicts for diff.save_changeset."""
    changes = []
    existing = list(TicketType.objects.filter(event=event, active=True))
    used = set()
    sold = inventory.reserved_by_type(event)
    for i, raw in enumerate(data.get('ticket_types') or []):
        proposed = _proposed_type(raw if isinstance(raw, dict) else {})
        if not proposed['name']:
            continue
        scored = sorted(((diff.similar(t.name, proposed['name']), t.pk, t) for t in existing if t.pk not in used),
                        key=lambda row: (-row[0], row[1]))
        match = scored[0][2] if scored and scored[0][0] >= 0.85 else None
        if match is None:
            changes.append({'entity': Change.ENTITY_TICKET_TYPE, 'op': Change.OP_CREATE, 'ref': f'tt:{i}',
                            'label': proposed['name'], 'data': proposed,
                            'human_fields': [f for f in TicketType.MONEY_FIELDS if f in proposed]})
            continue
        used.add(match.pk)
        updates = {}
        for f in TYPE_FIELDS:
            old, new = getattr(match, f), proposed[f]
            if f == 'quantity' and new is not None and new < sold.get(match.pk, 0):
                continue  # never below what's sold
            if f in match.locked_fields or old == new or (f == 'description' and not new):
                continue
            updates[f] = {'old': old, 'new': new}
        if updates:
            changes.append({'entity': Change.ENTITY_TICKET_TYPE, 'op': Change.OP_UPDATE, 'target_id': match.pk,
                            'label': match.name, 'data': updates,
                            'human_fields': [f for f in updates if f in TicketType.MONEY_FIELDS]})
    ts = inventory.ticketing(event)
    updates = {}
    for f in SETTINGS_FIELDS:
        new = str(data.get(f) or '').strip()[:5000]
        if new and new != getattr(ts, f) and f not in ts.locked_fields:
            updates[f] = {'old': getattr(ts, f), 'new': new}
    if updates:
        changes.append({'entity': Change.ENTITY_TICKET_SETTINGS, 'op': Change.OP_UPDATE, 'target_id': ts.pk,
                        'label': 'Ticket page texts', 'data': updates})
    return changes


def run_job(job):
    """IngestJob kind=tickets (scrape.jobs.run_job hands it here)."""
    event = job.event
    job.progress = 'Drafting tickets…'
    job.save(update_fields=['progress'])
    data = _call_model([{'role': 'system', 'content': PROMPT},
                        {'role': 'user', 'content': json.dumps({**_context(event), 'organizer_request':
                                                                job.instructions}, ensure_ascii=False)}])
    changes = compute_changes(event, data)
    cs = diff.save_changeset(event, job, changes, notes=str(data.get('notes') or '')[:2000])
    job.status = IngestJob.DONE
    job.progress = ''
    job.finished_at = timezone.now()
    job.result = {'changeset_id': cs.pk, 'pending': len(changes), 'summary': cs.summary, 'event_name': event.name}
    job.save()
    return cs


def apply_change(event, owner, ch, fields):
    """diff.apply_changeset → one accepted ticket change. Returns True if applied."""
    if ch.entity == Change.ENTITY_TICKET_SETTINGS:
        ts = inventory.ticketing(event)
        for f in (fields if fields is not None else ch.data.keys()):
            if f in ch.data and f in SETTINGS_FIELDS:
                setattr(ts, f, ch.data[f]['new'])
        ts.updated_at = timezone.now()
        ts.save()
        return True
    if ch.op == Change.OP_CREATE:
        data = {k: v for k, v in ch.data.items() if k in TYPE_FIELDS}
        order = (TicketType.objects.filter(event=event).order_by('-sort_order')
                 .values_list('sort_order', flat=True).first() or 0) + 1
        TicketType.objects.create(event=event, sort_order=order, **data)
        return True
    t = TicketType.objects.filter(pk=ch.target_id, event=event).first()
    if t is None:
        return False
    sold = inventory.reserved_by_type(event).get(t.pk, 0)
    for f in (fields if fields is not None else ch.data.keys()):
        if f not in ch.data or f not in TYPE_FIELDS:
            continue
        new = ch.data[f]['new']
        if f == 'quantity' and new is not None and new < sold:
            continue
        setattr(t, f, new)
    t.save()
    return True


def format_value(field, value):
    if field == 'price_cents' and isinstance(value, int):
        return money.fmt(value)
    if field == 'quantity' and value is None:
        return 'No limit'
    return None

