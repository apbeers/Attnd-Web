"""Ticket information other parts of Attnd show: the apps' bundle (API-017), Studio sites, history snapshots."""

from django.conf import settings
from django.urls import reverse
from django.utils.dateparse import parse_datetime

from tickets import inventory, money


def page_url(event):
    return settings.SITE_URL + reverse('tickets:page', args=[event.event_code])


def tickets_url(event):
    """The event's ticket page while ticketing is on (on sale or not yet), else None."""
    try:
        ts = event.ticketing
    except Exception:
        return None
    if not (inventory.plan_allows(event) and ts.enabled) or event.status == 'draft':
        return None
    return page_url(event)


def summary(event):
    """The bundle's `tickets` object, or None when the event doesn't sell tickets through Attnd."""
    url = tickets_url(event)
    if url is None:
        return None
    ts = event.ticketing
    state, _ = inventory.sales_state(event, ts)
    rows = [r for r in inventory.listing(event) if not r['type'].hidden]
    sellable = [r for r in rows if not r['type'].add_on]
    prices = [r['all_in_cents'] for r in sellable] or [0]
    low, high = min(prices), max(prices)
    if not sellable:
        text = ''
    elif high == 0:
        text = 'Free'
    elif low == high:
        text = money.fmt(low, ts.currency)
    else:
        text = f'From {money.fmt(low, ts.currency)}'
    return {
        'on_sale': state == 'open' and not ts.sold_out,
        'state': 'sold_out' if state == 'open' and ts.sold_out else state,
        'sold_out': ts.sold_out,
        'url': url,
        'min_price_cents': low,
        'max_price_cents': high,
        'currency': ts.currency,
        'price_text': text,
        'prices_include_fees': True,
    }


def site_tickets(event):
    """`tickets` in a Studio site's data.json (<attnd-tickets>): the list with all-in prices and the buy link."""
    info = summary(event)
    if info is None:
        return None
    ts = event.ticketing
    types = []
    for row in inventory.listing(event):
        t = row['type']
        if t.hidden:
            continue
        types.append({'id': t.id, 'name': t.name, 'description': t.description, 'kind': t.kind,
                      'price_cents': row['all_in_cents'], 'price_text': (
                          'Free' if t.kind == t.KIND_FREE or row['all_in_cents'] == 0 else
                          ('From ' if t.kind == t.KIND_DONATION else '') + money.fmt(row['all_in_cents'], ts.currency)),
                      'state': row['state'], 'add_on': t.add_on,
                      'remaining': row['remaining'] if row['remaining'] is not None and row['remaining'] <= 20 else None})
    return {**info, 'url': info['url'] + '?src=site', 'types': types}


# ---------------------------------------------------------------------------
# History snapshots (events.content.snapshot / restore): ticket types and the policy texts
# ---------------------------------------------------------------------------

SNAPSHOT_TYPE_FIELDS = ['name', 'description', 'kind', 'price_cents', 'quantity', 'min_per_order', 'max_per_order',
                        'hidden', 'unlock_code', 'add_on', 'reentry', 'transferable', 'sort_order', 'active']


def snapshot(event):
    from tickets.models import TicketSettings, TicketType
    ts = TicketSettings.objects.filter(event=event).first()
    if ts is None and not TicketType.objects.filter(event=event).exists():
        return None
    return {
        'settings': {f: getattr(ts, f) for f in ('page_text', 'refund_policy_text', 'faq')} if ts else {},
        'types': [{'id': t.id, **{f: getattr(t, f) for f in SNAPSHOT_TYPE_FIELDS},
                   'sales_start': t.sales_start.isoformat() if t.sales_start else None,
                   'sales_end': t.sales_end.isoformat() if t.sales_end else None}
                  for t in TicketType.objects.filter(event=event).order_by('id')],
    }


def restore(event, data):
    """Put ticket types back as they were. Types with sales are never deleted (hidden instead); tickets never change."""
    if not data:
        return
    from tickets.models import TicketType
    ts = inventory.ticketing(event)
    for f, v in (data.get('settings') or {}).items():
        if f in ('page_text', 'refund_policy_text', 'faq'):
            setattr(ts, f, v or '')
    ts.save()
    keep = set()
    for row in data.get('types') or []:
        t = TicketType.objects.filter(pk=row.get('id'), event=event).first() or TicketType(event=event)
        for f in SNAPSHOT_TYPE_FIELDS:
            if f in row:
                setattr(t, f, row[f])
        t.sales_start = parse_datetime(row['sales_start']) if row.get('sales_start') else None
        t.sales_end = parse_datetime(row['sales_end']) if row.get('sales_end') else None
        t.save()
        keep.add(t.pk)
    for t in TicketType.objects.filter(event=event).exclude(pk__in=keep):
        if t.tickets.exists() or t.order_items.exists():
            TicketType.objects.filter(pk=t.pk).update(active=False)
        else:
            t.delete()
