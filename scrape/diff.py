"""
Turn an AI extraction into reviewable changes, and apply the ones an organizer accepts.

Flow:
    current = current_state(event)                # sent to the AI so it can reuse ids
    data    = scrape_event(..., current=current)  # AI's view of the sources
    changes = compute_changes(event, data)        # list[dict] (not saved)
    cs      = save_changeset(event, job, changes, notes)
    apply_changeset(cs, decisions, user)          # decisions: {change_id: {"accept": bool, "fields": [...]}}

Rules:
- Fields an organizer edited by hand (``locked_fields``) are never silently overwritten — the change is
  proposed with ``conflict_fields`` and left unticked by default.
- Empty values from the AI never blank out existing data ("not found" ≠ "removed").
- Removals are only proposed when the AI cites evidence, and are never auto-applied.
- ``context`` (API-018, the assistant's details) is one field per event / location / item: the AI's view is merged
  onto the current one key by key (keys the AI left empty keep their value), and the review shows what changed.
"""

from datetime import datetime, timedelta
from decimal import Decimal
from difflib import SequenceMatcher
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from events import context as ctx
from events.content import batch_changes, save_revision
from events.models import Event, Location, ScheduledItem, normalize_tags
from scrape.models import Change, ChangeSet

EVENT_FIELDS = ['name', 'description', 'address', 'latitude', 'longitude', 'timezone', 'start_time', 'end_time']
LOCATION_FIELDS = ['name', 'snippet', 'description', 'category', 'website', 'image_url', 'address',
                   'latitude', 'longitude', 'tags']
ITEM_FIELDS = ['name', 'description', 'start_time', 'end_time', 'location', 'tags']
MAX_LEN = {'name': 256, 'description': 1024, 'snippet': 200, 'address': 512, 'category': 64,
           'website': 1024, 'image_url': 1024, 'timezone': 64}
EVENT_MAX_LEN = {**MAX_LEN, 'description': 2048}
MINOR_FIELDS = {'description', 'snippet', 'image_url', 'website', 'category', 'tags', 'latitude', 'longitude',
                'context'}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def tz_or_utc(name):
    try:
        return ZoneInfo(name) if name else ZoneInfo('UTC')
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo('UTC')


def valid_tz(name):
    if not name:
        return None
    try:
        return ZoneInfo(name).key
    except (ZoneInfoNotFoundError, ValueError):
        return None


def parse_local(raw, tz):
    """Parse an AI time string. Naive values are wall-clock times in ``tz``."""
    if not raw or not isinstance(raw, str):
        return None
    dt = parse_datetime(raw.strip())
    if dt is None:
        try:
            dt = datetime.fromisoformat(raw.strip())
        except ValueError:
            return None
    return dt if dt.tzinfo else dt.replace(tzinfo=tz)


def norm(text):
    return ' '.join(str(text or '').lower().replace('&', 'and').split())


def similar(a, b):
    return SequenceMatcher(None, norm(a), norm(b)).ratio()


def _clip(field, value, limits=MAX_LEN):
    if isinstance(value, str) and field in limits:
        return value.strip()[:limits[field]]
    return value


def _jsonable(value):
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return value


def _same(field, old, new):
    if field in ('latitude', 'longitude'):
        if old is None or new is None:
            return old is None and new is None
        return abs(float(old) - float(new)) < 0.0005  # ~50 m
    if isinstance(old, datetime) or isinstance(new, datetime):
        return old == new
    if field == 'tags':
        return sorted(normalize_tags(old)) == sorted(normalize_tags(new))
    if field == 'location':
        return old == new
    return norm(old) == norm(new) if isinstance(old, str) or isinstance(new, str) else old == new


def _empty(value):
    return value is None or value == '' or value == [] or value == {}


def _brief_context(kind, value):
    """A location's / item's context for CURRENT DATA without its long texts (keeps the prompt small)."""
    brief = ctx.normalize(kind, value)
    brief.pop('about', None)
    if 'performers' in brief:
        brief['performers'] = [{k: v for k, v in p.items() if k != 'bio'} for p in brief['performers']]
    return brief


def find_location(event):
    """name → id of one of the event's locations (exact, else ≥ 0.9 similar), for amenities named by the AI."""
    locations = list(event.location_set.values_list('id', 'name'))

    def find(name):
        key = norm(name)
        for loc_id, loc_name in locations:
            if norm(loc_name) == key:
                return loc_id
        best = max(locations, key=lambda row: similar(row[1], name), default=None)
        return best[0] if best and similar(best[1], name) >= 0.9 else None
    return find


def current_state(event):
    """Compact JSON of the event as it is now, given to the AI on refresh."""
    from scrape.tags import VOCABULARY_HINT, event_vocabulary
    names = dict(event.location_set.values_list('id', 'name'))
    return {
        'tag_vocabulary': event_vocabulary(event)[:VOCABULARY_HINT],
        'event': {
            'name': event.name, 'description': event.description, 'address': event.address,
            'timezone': event.timezone,
            'start_time': event.start_time.astimezone(tz_or_utc(event.timezone)).replace(tzinfo=None).isoformat(),
            'end_time': (event.end_time.astimezone(tz_or_utc(event.timezone)).replace(tzinfo=None).isoformat()
                         if event.end_time else None),
            'context': ctx.normalize_named(ctx.EVENT, event.context, names),
        },
        'locations': [
            {'id': loc.id, 'name': loc.name, 'snippet': loc.snippet, 'category': loc.category,
             'address': loc.address, 'tags': normalize_tags(loc.tags),
             **({'context': c} if (c := _brief_context(ctx.LOCATION, loc.context)) else {})}
            for loc in event.location_set.all()
        ],
        'schedule': [
            {'id': it.id, 'name': it.name,
             'start_time': it.start_time.astimezone(tz_or_utc(event.timezone)).replace(tzinfo=None).isoformat(),
             'location': it.location.name if it.location_id else None, 'tags': normalize_tags(it.tags),
             **({'context': c} if (c := _brief_context(ctx.ITEM, it.context)) else {})}
            for it in event.scheduleditem_set.select_related('location')
        ],
    }


# ---------------------------------------------------------------------------
# Diff
# ---------------------------------------------------------------------------

def _field_changes(obj, proposed, fields):
    """Return ({field: {old, new}}, [conflict fields]) for fields that differ and are non-empty."""
    out, conflicts = {}, []
    locked = set(obj.locked_fields or [])
    for f in fields:
        if f not in proposed:
            continue
        new = proposed[f]
        old = getattr(obj, f) if f != 'location' else obj.location_id
        if _empty(new) or _same(f, old, new):
            continue
        out[f] = {'old': _jsonable(old), 'new': _jsonable(new)}
        if f in locked:
            conflicts.append(f)
    return out, conflicts


def _context_change(kind, obj, proposed, fields, conflicts, names=None):
    """Add a ``context`` field change when the AI's context (merged onto the current one) differs."""
    current = ctx.normalize_named(kind, obj.context, names)
    merged = ctx.merge(kind, current, proposed)
    if merged == current:
        return
    fields['context'] = {'old': current, 'new': merged}
    if 'context' in (obj.locked_fields or []):
        conflicts.append('context')


def _is_minor(fields_changed):
    keys = set(fields_changed)
    if keys <= MINOR_FIELDS:
        return True
    if keys == {'name'}:
        ch = fields_changed['name']
        return similar(ch['old'], ch['new']) >= 0.85
    return False


def compute_changes(event, data):
    """Compare an extraction with the event's current content. Returns a list of change dicts."""
    tz_name = data.get('timezone') or event.timezone
    tz = tz_or_utc(tz_name)
    changes = []

    # ---- Event ----
    ev_proposed = {
        'name': _clip('name', data.get('name')),
        'description': _clip('description', data.get('description'), EVENT_MAX_LEN),
        'address': _clip('address', data.get('address')),
        'latitude': data.get('latitude'),
        'longitude': data.get('longitude'),
        'timezone': valid_tz(data.get('timezone')),
        'start_time': parse_local(data.get('start_time'), tz),
        'end_time': parse_local(data.get('end_time'), tz),
    }
    fields, conflicts = _field_changes(event, ev_proposed, EVENT_FIELDS)
    _context_change(ctx.EVENT, event, data.get('context'), fields, conflicts,
                    names=dict(event.location_set.values_list('id', 'name')))
    if fields:
        changes.append({'entity': Change.ENTITY_EVENT, 'op': Change.OP_UPDATE, 'target_id': event.id,
                        'label': event.name, 'data': fields, 'conflict_fields': conflicts,
                        'minor': _is_minor(fields)})

    # ---- Locations ----
    existing_locs = {loc.id: loc for loc in event.location_set.all()}
    claimed = set()
    name_to_target = {}  # extracted location name (normalized) -> {"id": x} or {"ref": "loc:n"}

    def match_location(loc):
        eid = loc.get('existing_id')
        if eid in existing_locs and eid not in claimed:
            return existing_locs[eid]
        best, best_score = None, 0.0
        for cand in existing_locs.values():
            if cand.id in claimed:
                continue
            score = similar(cand.name, loc.get('name'))
            if score > best_score:
                best, best_score = cand, score
        return best if best_score >= 0.9 else None

    new_count = 0
    for loc in data.get('locations', []):
        if not loc.get('name'):
            continue
        proposed = {f: _clip(f, loc.get(f)) for f in LOCATION_FIELDS}
        proposed['tags'] = normalize_tags(loc.get('tags'))
        target = match_location(loc)
        if target is not None:
            claimed.add(target.id)
            name_to_target[norm(loc['name'])] = {'id': target.id, 'name': target.name}
            fields, conflicts = _field_changes(target, proposed, LOCATION_FIELDS)
            _context_change(ctx.LOCATION, target, loc.get('context'), fields, conflicts)
            if fields:
                changes.append({'entity': Change.ENTITY_LOCATION, 'op': Change.OP_UPDATE,
                                'target_id': target.id, 'label': target.name, 'data': fields,
                                'conflict_fields': conflicts, 'minor': _is_minor(fields)})
        else:
            new_count += 1
            ref = f'loc:{new_count}'
            name_to_target[norm(loc['name'])] = {'ref': ref, 'name': proposed['name']}
            proposed['context'] = ctx.normalize(ctx.LOCATION, loc.get('context'))
            changes.append({'entity': Change.ENTITY_LOCATION, 'op': Change.OP_CREATE, 'ref': ref,
                            'label': proposed['name'],
                            'data': {k: v for k, v in proposed.items() if not _empty(v)},
                            'conflict_fields': [], 'minor': False})

    for removal in data.get('removed_location_ids', []):
        loc = existing_locs.get(removal.get('id'))
        if loc is not None and loc.id not in claimed:
            changes.append({'entity': Change.ENTITY_LOCATION, 'op': Change.OP_DELETE, 'target_id': loc.id,
                            'label': loc.name, 'data': {}, 'reason': removal.get('reason', ''),
                            'conflict_fields': [], 'minor': False})

    # ---- Schedule ----
    existing_items = {it.id: it for it in event.scheduleditem_set.select_related('location')}
    claimed_items = set()

    def resolve_location(name):
        if not name:
            return None
        key = norm(name)
        if key in name_to_target:
            return name_to_target[key]
        for loc in existing_locs.values():
            if norm(loc.name) == key or similar(loc.name, name) >= 0.9:
                return {'id': loc.id, 'name': loc.name}
        return None

    def match_item(item, start):
        eid = item.get('existing_id')
        if eid in existing_items and eid not in claimed_items:
            return existing_items[eid]
        for cand in existing_items.values():
            if cand.id in claimed_items:
                continue
            if similar(cand.name, item.get('name')) >= 0.9 and (start is None or cand.start_time == start):
                return cand
        return None

    for item in data.get('schedule', []):
        if not item.get('name'):
            continue
        start = parse_local(item.get('start_time'), tz)
        loc_target = resolve_location(item.get('location_name'))
        target = match_item(item, start)
        proposed = {
            'name': _clip('name', item.get('name')),
            'description': _clip('description', item.get('description')),
            'start_time': start,
            'end_time': parse_local(item.get('end_time'), tz),
            'tags': normalize_tags(item.get('tags')),
        }
        if target is not None:
            claimed_items.add(target.id)
            if loc_target and 'id' in loc_target:
                proposed['location'] = loc_target['id']
            fields, conflicts = _field_changes(target, proposed, ITEM_FIELDS)
            _context_change(ctx.ITEM, target, item.get('context'), fields, conflicts)
            if loc_target and 'ref' in loc_target:
                fields['location'] = {'old': target.location_id, 'new': loc_target['ref']}
            if 'location' in fields:
                old_loc = existing_locs.get(fields['location']['old'])
                fields['location']['old_name'] = old_loc.name if old_loc else None
                fields['location']['new_name'] = loc_target['name'] if loc_target else None
                if 'location' in (target.locked_fields or []):
                    conflicts.append('location')
            if fields:
                changes.append({'entity': Change.ENTITY_ITEM, 'op': Change.OP_UPDATE, 'target_id': target.id,
                                'label': target.name, 'data': fields, 'conflict_fields': conflicts,
                                'minor': _is_minor(fields)})
        else:
            if start is None:
                continue  # an item without a time can't go on the schedule
            data_out = {k: _jsonable(v) for k, v in proposed.items() if not _empty(v)}
            if item_context := ctx.normalize(ctx.ITEM, item.get('context')):
                data_out['context'] = item_context
            if loc_target:
                data_out['location'] = loc_target.get('id') or loc_target.get('ref')
                data_out['location_name'] = loc_target['name']
            changes.append({'entity': Change.ENTITY_ITEM, 'op': Change.OP_CREATE, 'label': proposed['name'],
                            'data': data_out, 'conflict_fields': [], 'minor': False})

    for removal in data.get('removed_item_ids', []):
        it = existing_items.get(removal.get('id'))
        if it is not None and it.id not in claimed_items:
            changes.append({'entity': Change.ENTITY_ITEM, 'op': Change.OP_DELETE, 'target_id': it.id,
                            'label': it.name, 'data': {}, 'reason': removal.get('reason', ''),
                            'conflict_fields': [], 'minor': False})
    return changes


def summarize(changes):
    counts = {}
    for c in changes:
        key = (c['op'], c['entity'])
        counts[key] = counts.get(key, 0) + 1
    words = {'event': ('event detail', 'event details'), 'location': ('location', 'locations'),
             'item': ('schedule item', 'schedule items')}
    verbs = {'create': 'new', 'update': 'updated', 'delete': 'removed'}
    parts = []
    for (op, entity), n in sorted(counts.items(), key=lambda kv: ('create', 'update', 'delete').index(kv[0][0])):
        noun = words[entity][0 if n == 1 else 1]
        parts.append(f'{n} {verbs[op]} {noun}' if entity != 'event' else 'event details updated')
    return ', '.join(parts) or 'No changes'


def save_changeset(event, job, changes, notes=''):
    order = {'event': 0, 'location': 1, 'item': 2}
    cs = ChangeSet.objects.create(
        event=event, job=job, notes=notes or '',
        base_version=Event.objects.filter(pk=event.pk).values_list('content_version', flat=True).first() or 0,
        status=ChangeSet.PENDING if changes else ChangeSet.EMPTY,
        summary=summarize(changes),
    )
    Change.objects.bulk_create([
        Change(changeset=cs, sort=order[c['entity']] * 10000 + i, entity=c['entity'], op=c['op'],
               target_id=c.get('target_id'), ref=c.get('ref', ''), label=(c.get('label') or '')[:256],
               data=c['data'], conflict_fields=c.get('conflict_fields', []), reason=c.get('reason', ''),
               human_fields=c.get('human_fields', []),
               minor=c.get('minor', False))
        for i, c in enumerate(changes)
    ])
    return cs


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------

def _value(field, value):
    if field in ('start_time', 'end_time'):
        return parse_datetime(value) if isinstance(value, str) else value
    return value


def default_decisions(changeset, minor_only=False):
    """Accept everything except conflicting fields (and, with minor_only, anything non-minor)."""
    decisions = {}
    for ch in changeset.changes.all():
        if minor_only and not (ch.minor and not ch.conflict_fields and ch.op == Change.OP_UPDATE):
            continue
        if ch.human_fields and ch.op != Change.OP_UPDATE:
            continue  # money: a person ticks it
        fields = ([f for f in ch.data if f not in ch.conflict_fields and f not in ch.human_fields]
                  if ch.op == Change.OP_UPDATE else None)
        if ch.op == Change.OP_UPDATE and not fields:
            continue
        decisions[ch.id] = {'accept': True, 'fields': fields}
    return decisions


def apply_changeset(changeset, decisions, user=None, partial_ok=False):
    """
    Apply accepted changes in one transaction. ``decisions`` maps change id → {"accept", "fields"}.
    Changes not in ``decisions`` are rejected unless ``partial_ok`` (leaves them pending).
    Returns the number of changes applied.
    """
    event = changeset.event
    owner = user or event.user  # who applied it, else the owner (may be None if unassigned)
    applied = 0
    with transaction.atomic():
        save_revision(event, f'Before applying AI changes: {changeset.summary}'[:256], user)
        ref_ids = {}
        changes = list(changeset.changes.filter(status=Change.PENDING))
        # Order: location creates/updates, item creates/updates, the event (its amenities may name a new
        # location), then deletions
        changes.sort(key=lambda c: (c.op == Change.OP_DELETE, c.entity == Change.ENTITY_EVENT, c.sort))
        with batch_changes(event):
            for ch in changes:
                decision = decisions.get(ch.id) or decisions.get(str(ch.id))
                if not decision or not decision.get('accept'):
                    if not partial_ok:
                        ch.status = Change.REJECTED
                        ch.save(update_fields=['status'])
                    continue
                fields = decision.get('fields')
                if partial_ok and fields is not None and ch.op == Change.OP_UPDATE:
                    _keep_the_rest_for_review(ch, fields)
                if _apply_one(event, owner, ch, fields, ref_ids):
                    applied += 1
                    ch.status = Change.ACCEPTED
                    ch.accepted_fields = fields
                else:
                    ch.status = Change.REJECTED
                ch.save(update_fields=['status', 'accepted_fields'])
        remaining = changeset.changes.filter(status=Change.PENDING).exists()
        if not remaining:
            changeset.status = ChangeSet.APPLIED if applied else ChangeSet.DISCARDED
            changeset.applied_at = timezone.now()
            changeset.applied_by = user if (user and user.is_authenticated) else None
            changeset.save()
    return applied


def _keep_the_rest_for_review(ch, fields):
    """An automatic apply takes only some fields of an update (never locked / hand-edited ones): the others become a
    pending change of their own, so a conflict such as the event's dates still waits in Review instead of vanishing
    with the accepted part."""
    rest = {k: v for k, v in ch.data.items() if k not in fields}
    if not rest:
        return
    Change.objects.create(changeset=ch.changeset, entity=ch.entity, op=ch.op, target_id=ch.target_id, ref=ch.ref,
                          label=ch.label, data=rest, conflict_fields=[f for f in ch.conflict_fields if f in rest],
                          human_fields=[f for f in ch.human_fields if f in rest], reason=ch.reason, minor=False,
                          sort=ch.sort)
    ch.data = {k: v for k, v in ch.data.items() if k in fields}
    ch.conflict_fields = [f for f in ch.conflict_fields if f in fields]
    ch.human_fields = [f for f in ch.human_fields if f in fields]
    ch.save(update_fields=['data', 'conflict_fields', 'human_fields'])


def _apply_one(event, owner, ch, fields, ref_ids):
    if ch.entity == Change.ENTITY_EVENT and ch.op == Change.OP_UPDATE:
        for f in (fields if fields is not None else ch.data.keys()):
            if f in ch.data and f in EVENT_FIELDS:
                setattr(event, f, _value(f, ch.data[f]['new']))
            elif f == 'context' and f in ch.data:
                event.context = ctx.resolve_named(ctx.EVENT, ch.data[f]['new'], find_location(event))
        event.save()
        return True

    if ch.entity == Change.ENTITY_LOCATION:
        if ch.op == Change.OP_CREATE:
            loc = Location(event=event, user=owner,
                           sort_order=(event.location_set.order_by('-sort_order')
                                       .values_list('sort_order', flat=True).first() or 0) + 1)
            for f, v in ch.data.items():
                if f in LOCATION_FIELDS or f == 'context':
                    setattr(loc, f, v)
            loc.save()
            ref_ids[ch.ref] = loc.id
            return True
        loc = Location.objects.filter(pk=ch.target_id, event=event).first()
        if loc is None:
            return False
        if ch.op == Change.OP_DELETE:
            loc.delete()
            return True
        for f in (fields if fields is not None else ch.data.keys()):
            if f in ch.data and (f in LOCATION_FIELDS or f == 'context'):
                setattr(loc, f, ch.data[f]['new'])
        loc.save()
        return True

    if ch.entity == Change.ENTITY_ITEM:
        def loc_id(value):
            if isinstance(value, str) and value.startswith('loc:'):
                return ref_ids.get(value)  # None if that new location was rejected
            if value and Location.objects.filter(pk=value, event=event).exists():
                return value
            return None

        if ch.op == Change.OP_CREATE:
            item = ScheduledItem(event=event, user=owner)
            for f, v in ch.data.items():
                if f == 'location':
                    item.location_id = loc_id(v)
                elif f in ITEM_FIELDS or f == 'context':
                    setattr(item, f, _value(f, v))
            if item.start_time is None:
                return False
            item.save()
            return True
        item = ScheduledItem.objects.filter(pk=ch.target_id, event=event).first()
        if item is None:
            return False
        if ch.op == Change.OP_DELETE:
            item.delete()
            return True
        for f in (fields if fields is not None else ch.data.keys()):
            if f not in ch.data or (f not in ITEM_FIELDS and f != 'context'):
                continue
            if f == 'location':
                item.location_id = loc_id(ch.data[f]['new'])
            else:
                setattr(item, f, _value(f, ch.data[f]['new']))
        item.save()
        return True
    return False


# ---------------------------------------------------------------------------
# Create a brand-new event from an extraction
# ---------------------------------------------------------------------------

def create_event_from_extraction(data, owner, *, status, public, fallback_tz='UTC', source_urls=(),
                                 source_text=''):
    from events.models import generate_event_code

    tz_name = data.get('timezone') or fallback_tz
    tz = tz_or_utc(tz_name)
    start = parse_local(data.get('start_time'), tz)
    notes = []
    if start is None:
        # Fall back to the first schedule item, then to "a week from now" and flag it for the organizer
        item_starts = [parse_local(i.get('start_time'), tz) for i in data.get('schedule', [])]
        item_starts = [s for s in item_starts if s]
        start = min(item_starts) if item_starts else timezone.now() + timedelta(days=7)
        notes.append("The start time wasn't in the sources — please check it.")

    with transaction.atomic():
        event = Event.objects.create(
            name=_clip('name', data['name']) or 'Untitled event',
            event_code=generate_event_code(),
            description=_clip('description', data.get('description') or '', EVENT_MAX_LEN),
            address=_clip('address', data.get('address') or ''),
            latitude=data.get('latitude'),
            longitude=data.get('longitude'),
            start_time=start,
            end_time=parse_local(data.get('end_time'), tz),
            timezone=tz.key,
            status=status,
            public=public,
            paid=False,
            user=owner,
            source_urls=list(source_urls),
            source_text=source_text,
            last_refreshed_at=timezone.now(),
        )
        name_to_loc = {}
        for i, loc in enumerate(data.get('locations', [])):
            if not loc.get('name'):
                continue
            obj = Location.objects.create(
                event=event, user=owner, sort_order=i,
                **{f: _clip(f, loc.get(f)) if loc.get(f) is not None else
                   ('' if f in ('snippet', 'description', 'address', 'category') else None)
                   for f in LOCATION_FIELDS if f != 'tags'},
                tags=normalize_tags(loc.get('tags')),
                context=ctx.normalize(ctx.LOCATION, loc.get('context')),
            )
            name_to_loc[norm(obj.name)] = obj
        for item in data.get('schedule', []):
            start_i = parse_local(item.get('start_time'), tz)
            if not item.get('name') or start_i is None:
                continue
            loc = name_to_loc.get(norm(item.get('location_name')))
            if loc is None and item.get('location_name'):
                loc = next((l for k, l in name_to_loc.items() if similar(k, item['location_name']) >= 0.9), None)
            ScheduledItem.objects.create(
                event=event, user=owner, location=loc,
                name=_clip('name', item['name']),
                description=_clip('description', item.get('description') or ''),
                start_time=start_i,
                end_time=parse_local(item.get('end_time'), tz),
                tags=normalize_tags(item.get('tags')),
                context=ctx.normalize(ctx.ITEM, item.get('context')),
            )
        # API-018: the event's context last, so its amenities can point at the new locations (no version bump).
        event_context = ctx.resolve_named(ctx.EVENT, ctx.normalize_named(ctx.EVENT, data.get('context')),
                                          find_location(event))
        if event_context:
            Event.objects.filter(pk=event.pk).update(context=event_context)
        event.refresh_from_db()
        save_revision(event, 'Created by AI import')
    return event, notes
