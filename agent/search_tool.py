"""Search everything from the chat (stage I13): "where did I mention parking?" across the person's events, their
locations, schedule items, notifications and the details for the assistant (Postgres full text + trigrams,
attnd/search.py). Returns compact JSON lines with ids, so the model can show the matching cards."""

import json

TOOL = {
    'name': 'search_everything',
    'description': 'Search the person\'s events, locations, schedule items, notifications and the details for the '
                   'assistant (FAQ, policies, menus, performers…) for words or a phrase, typo-tolerant. For "where did '
                   'I mention X?", "which item talks about Y?". Results carry ids for show.',
    'parameters': {'type': 'object', 'additionalProperties': False, 'required': ['query', 'event_id'], 'properties': {
        'query': {'type': 'string'},
        'event_id': {'type': ['integer', 'null'], 'description': 'only this event, or null for all of them'}}},
    'strict': True,
}

LIMIT = 8


def _snippet(text, query, width=140):
    text = ' '.join(str(text or '').split())
    lower = text.lower()
    for word in sorted(query.lower().split(), key=len, reverse=True):
        i = lower.find(word)
        if i >= 0:
            start = max(0, i - width // 3)
            return ('…' if start else '') + text[start:start + width] + ('…' if start + width < len(text) else '')
    return text[:width] + ('…' if len(text) > width else '')


def search(user, query, event_id=None):
    from django.db.models import Q

    from attnd.search import search as fts
    from events.models import Event, EventMember, Location, Notification, ScheduledItem
    events = Event.objects.filter(Q(user=user) | Q(id__in=EventMember.objects.filter(user=user).values('event_id')))
    if event_id:
        events = events.filter(pk=event_id)
    ids = list(events.values_list('pk', flat=True))
    out = []
    for row in fts(Event.objects.filter(pk__in=ids), query, {'name': 'A', 'description': 'B', 'address': 'C',
                                                            'context::json': 'C'})[:LIMIT]:
        out.append({'kind': 'event', 'event_id': row.pk, 'name': row.name,
                    'match': _snippet(f'{row.description} {json.dumps(row.context, ensure_ascii=False)}', query)})
    for row in fts(Location.objects.filter(event_id__in=ids), query, {
            'name': 'A', 'category': 'B', 'snippet': 'B', 'description': 'C', 'address': 'C', 'context::json': 'C'})[:LIMIT]:
        out.append({'kind': 'location', 'event_id': row.event_id, 'location_id': row.pk, 'name': row.name,
                    'match': _snippet(f'{row.snippet} {row.description} {json.dumps(row.context, ensure_ascii=False)}', query)})
    for row in fts(ScheduledItem.objects.filter(event_id__in=ids), query, {
            'name': 'A', 'description': 'C', 'context::json': 'C'})[:LIMIT]:
        out.append({'kind': 'schedule_item', 'event_id': row.event_id, 'item_id': row.pk, 'name': row.name,
                    'match': _snippet(f'{row.description} {json.dumps(row.context, ensure_ascii=False)}', query)})
    for row in fts(Notification.objects.filter(event_id__in=ids), query, {'title': 'A', 'body': 'B'})[:LIMIT]:
        out.append({'kind': 'notification', 'event_id': row.event_id, 'notification_id': row.pk, 'name': row.title,
                    'match': _snippet(row.body, query)})
    return out


def tool(run, call, args):
    from agent import loop
    query = str(args.get('query') or '').strip()[:200]
    if not query:
        return 'Error: empty query.'
    event_id = args.get('event_id') if isinstance(args.get('event_id'), int) else None
    found = search(run.user, query, event_id)
    loop.add_step(run, tool='search_everything', call_id=call.id, arguments={'query': query, 'event_id': event_id},
                  text=f'Searched for “{query}” ({len(found)} found)', result=json.dumps(found)[:4000])
    if not found:
        return f'Nothing mentions “{query}”.'
    return 'RESULTS (data):\n' + '\n'.join(json.dumps(r, ensure_ascii=False) for r in found)
