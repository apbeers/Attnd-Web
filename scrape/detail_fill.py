"""The detail-fill pass (batch 7 A1, owner Nov 22, 2026).

The main extraction reads the whole site in one call and, on big events, leaves most schedule items and places
without "Details for the assistant" (API-018 context): 2VLQITGR had them on 2 of 43 items. After it (imports and
refreshes, and on its own from "Fill in details"), every item / place whose context is empty gets a second, focused
look: batches of `BATCH` entities, each with only the source text that mentions it (windows around its name in the
full, untruncated crawl; the whole crawl trimmed when nothing mentions it), Claude Haiku structured output asking for
the context only. What comes back is normalised like every context (`events.context.normalize`) and only ever fills an
empty context: a person's details (locked) and the AI's own earlier ones are never replaced; refreshes still go
through the review (scrape.diff) like any AI change."""
import logging
import re

logger = logging.getLogger(__name__)

BATCH = 15
WINDOW = 900           # characters either side of a mention
PER_ENTITY = 3500      # source text per entity
FALLBACK_CHARS = 12000  # when nothing mentions any of a batch, this much of the crawl
MAX_ENTITIES = 150     # per run (cost)

SYSTEM = (
    "You add attendee details for an event app. For each entry, read ONLY its source excerpts and fill the context "
    "fields you can support from them (about, performers with short bios and lower-case genres, price, age limit, "
    "accessibility, links; for places also menu with prices, hours). Leave a field empty when the excerpts don't say "
    "it. Never invent facts, prices, times or links; links must appear in the excerpts and be https. Use the entry's "
    "ref exactly."
)


def _empty(ctx):
    return not any(v for v in (ctx or {}).values())


def _schema():
    from scrape.service import _ITEM_CONTEXT, _LOCATION_CONTEXT
    def rows(ctx):
        return {'type': 'array', 'items': {'type': 'object', 'additionalProperties': False,
                                           'required': ['ref', 'context'],
                                           'properties': {'ref': {'type': 'string'}, 'context': ctx}}}
    return {'type': 'object', 'additionalProperties': False, 'required': ['items', 'places'],
            'properties': {'items': rows(_ITEM_CONTEXT), 'places': rows(_LOCATION_CONTEXT)}}


def excerpts(name, content):
    """The parts of the crawl that mention `name` (case-insensitive; also its first words for long names)."""
    name = (name or '').strip()
    if len(name) < 3 or not content:
        return ''
    keys = [name]
    words = name.split()
    if len(words) > 3:
        keys.append(' '.join(words[:3]))
    spans = []
    lower = content.lower()
    for key in keys:
        for m in re.finditer(re.escape(key.lower()), lower):
            spans.append((max(0, m.start() - WINDOW), min(len(content), m.end() + WINDOW)))
        if spans:
            break
    spans.sort()
    merged = []
    for a, b in spans:
        if merged and a <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(b, merged[-1][1]))
        else:
            merged.append((a, b))
    out = ' … '.join(content[a:b] for a, b in merged)
    return out[:PER_ENTITY]


def targets(data):
    """[(ref, kind, entry)] of the extraction's items and places with an empty context."""
    out = []
    for i, item in enumerate(data.get('schedule') or []):
        if _empty(item.get('context')):
            out.append((f's{i}', 'item', item))
    for i, loc in enumerate(data.get('locations') or []):
        if _empty(loc.get('context')):
            out.append((f'l{i}', 'place', loc))
    return out[:MAX_ENTITIES]


def _ask(batch, content):
    from attnd import claude
    lines, any_mention = [], False
    for ref, kind, entry in batch:
        text = excerpts(entry.get('name'), content)
        any_mention = any_mention or bool(text)
        where = entry.get('location_name') or entry.get('category') or ''
        lines.append(f'### {ref} ({kind}) {entry.get("name")}' + (f' — {where}' if where else '')
                     + f'\n{text or "(no excerpt mentions it)"}')
    body = '\n\n'.join(lines)
    if not any_mention:
        body += '\n\n### Source text (trimmed)\n' + content[:FALLBACK_CHARS]
    data, cost = claude.structured(system=SYSTEM, content=[{'type': 'text', 'text': body}], schema=_schema(),
                                   model=claude.fast_model(), effort='low', max_tokens=16000, timeout=180.0)
    return data, cost


def fill(data, content, progress=None):
    """Fill empty contexts of the extraction dict in place. Returns {'items': n, 'places': n} filled."""
    from attnd import claude
    from events import context as ctx
    found = targets(data)
    done = {'items': 0, 'places': 0}
    if not found or not content or not claude.available():
        return done
    update = getattr(progress, 'update', None) or (lambda *a, **k: None)
    by_ref = {ref: (kind, entry) for ref, kind, entry in found}
    for start in range(0, len(found), BATCH):
        batch = found[start:start + BATCH]
        update(f'Adding details: {min(start + BATCH, len(found))} of {len(found)}…',
               done=_done_text(done))
        try:
            answer, _ = _ask(batch, content)
        except Exception as exc:  # one failed batch never sinks the import
            logger.warning('Detail fill batch failed: %s', exc)
            continue
        for key, kind_name in (('items', 'item'), ('places', 'place')):
            for row in (answer or {}).get(key) or []:
                kind, entry = by_ref.get(str(row.get('ref')), (None, None))
                if entry is None or kind != kind_name or not _empty(entry.get('context')):
                    continue
                cleaned = ctx.normalize(ctx.ITEM if kind == 'item' else ctx.LOCATION, row.get('context') or {})
                if cleaned and not _empty(cleaned):
                    entry['context'] = cleaned
                    done['items' if kind == 'item' else 'places'] += 1
    return done


def _done_text(done):
    parts = []
    if done['items']:
        parts.append(f"{done['items']} schedule item{'s' if done['items'] != 1 else ''}")
    if done['places']:
        parts.append(f"{done['places']} place{'s' if done['places'] != 1 else ''}")
    return 'Added details for ' + ' and '.join(parts) if parts else 'No more details found on the website'


def done_text(done):
    return _done_text(done)


def fill_event(event, content, progress=None):
    """"Fill in details" on its own: the event's items and places with an empty, unlocked context get one; saved on
    the rows (the draft) like any AI work. Returns the counts."""
    data = {'schedule': [], 'locations': []}
    rows = {}
    for item in event.scheduleditem_set.select_related('location'):
        if _empty(item.context) and 'context' not in (item.locked_fields or []):
            rows[f's{len(data["schedule"])}'] = item
            data['schedule'].append({'name': item.name, 'context': {},
                                     'location_name': item.location.name if item.location_id else ''})
    for loc in event.location_set.all():
        if _empty(loc.context) and 'context' not in (loc.locked_fields or []):
            rows[f'l{len(data["locations"])}'] = loc
            data['locations'].append({'name': loc.name, 'category': loc.category or '', 'context': {}})
    done = fill(data, content, progress)
    for ref, obj in rows.items():
        entry = (data['schedule'] if ref[0] == 's' else data['locations'])[int(ref[1:])]
        if not _empty(entry.get('context')):
            obj.context = entry['context']
            obj.save(update_fields=['context'])
    return done
