"""Rich context for the apps' on-device assistant (API-018).

One normaliser for the ``context`` JSON of an event, a location and a schedule item, used when saving, when
serialising the bundle, by the AI import/refresh and by the console. The shape is the API contract (API_V2.md →
Context):

- strings are trimmed; over-long text is cut at a word boundary (ending in "…", within the limit);
- empty strings, empty lists and incomplete entries are dropped; unknown keys are ignored;
- links are absolute https URLs with a host only;
- an object with nothing left is ``{}`` in storage and ``null`` in the bundle.

Only attendee-facing facts belong here (what the event's own pages say); never organizer-private data.
"""

import json
import logging
import re
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

EVENT, LOCATION, ITEM = 'event', 'location', 'item'

AMENITY_KINDS = ('restrooms', 'water', 'atm', 'first_aid', 'charging', 'lockers', 'info', 'accessible_entrance',
                 'food', 'merch', 'parking', 'shuttle', 'other')

# Limits (characters / entries). The contract's limits, plus the lengths of the parts inside list entries.
EVENT_TEXT = {'about': 4000, 'accessibility': 1500, 'policies': 1500, 'getting_there': 1500, 'weather_plan': 600}
LOCATION_TEXT = {'about': 2000, 'hours': 300, 'accessibility': 300, 'age_limit': 300}
ITEM_TEXT = {'about': 2000, 'age_limit': 300, 'price': 300, 'accessibility': 300}
MAX_FAQ, MAX_AMENITIES, MAX_MENU, MAX_PERFORMERS, MAX_GENRES = 40, 60, 40, 10, 5
MAX_LINKS = {EVENT: 10, LOCATION: 5, ITEM: 5}
FAQ_Q, FAQ_A = 300, 1000
NAME, NOTE, PRICE, BIO, GENRE, LINK_TITLE, URL = 120, 300, 40, 600, 32, 120, 1024

# Key order of each object (the bundle keeps it).
EVENT_KEYS = ('about', 'faq', 'amenities', 'accessibility', 'policies', 'getting_there', 'weather_plan', 'links')
LOCATION_KEYS = ('about', 'menu', 'hours', 'accessibility', 'age_limit', 'links')
ITEM_KEYS = ('about', 'performers', 'age_limit', 'price', 'accessibility', 'links')
KEYS = {EVENT: EVENT_KEYS, LOCATION: LOCATION_KEYS, ITEM: ITEM_KEYS}
TEXTS = {EVENT: EVENT_TEXT, LOCATION: LOCATION_TEXT, ITEM: ITEM_TEXT}

# The bundle's budget for all context together (JSON, UTF-8).
BUNDLE_BUDGET_BYTES = 150_000


# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------

def cut(text, limit):
    """``text`` of at most ``limit`` characters, cut at a word boundary with "…" when it was longer."""
    if len(text) <= limit:
        return text
    head = text[:limit - 1]
    space = max(head.rfind(' '), head.rfind('\n'))
    if space >= limit * 0.6:
        head = head[:space]
    return head.rstrip(' \n\t,;:-–—') + '…'


def _line(value, limit):
    """One line of text: whitespace collapsed, trimmed, cut."""
    if not isinstance(value, str):
        return ''
    return cut(' '.join(value.split()), limit)


def _block(value, limit):
    """Paragraph text: spaces collapsed per line, at most one empty line in a row, trimmed, cut."""
    if not isinstance(value, str):
        return ''
    lines = [' '.join(line.split()) for line in value.replace('\r\n', '\n').replace('\r', '\n').split('\n')]
    text = re.sub(r'\n{3,}', '\n\n', '\n'.join(lines)).strip()
    return cut(text, limit)


def https_url(value):
    """``value`` when it is an absolute https URL with a host (trimmed), else ''."""
    if not isinstance(value, str):
        return ''
    url = value.strip()
    if len(url) > URL or any(c.isspace() for c in url):
        return ''
    try:
        parsed = urlparse(url)
    except ValueError:
        return ''
    return url if parsed.scheme == 'https' and parsed.hostname else ''


def _list(value):
    return value if isinstance(value, list) else []


def _dict(value):
    return value if isinstance(value, dict) else {}


# ---------------------------------------------------------------------------
# Entries
# ---------------------------------------------------------------------------

def _links(value, limit):
    out, seen = [], set()
    for raw in _list(value):
        raw = _dict(raw)
        url = https_url(raw.get('url'))
        if not url or url in seen:
            continue
        seen.add(url)
        out.append({'title': _line(raw.get('title'), LINK_TITLE) or urlparse(url).hostname, 'url': url})
        if len(out) == limit:
            break
    return out


def _faq(value):
    out = []
    for raw in _list(value):
        raw = _dict(raw)
        q, a = _line(raw.get('q'), FAQ_Q), _block(raw.get('a'), FAQ_A)
        if q and a:
            out.append({'q': q, 'a': a})
        if len(out) == MAX_FAQ:
            break
    return out


def amenity_kind(value):
    kind = '_'.join(str(value or '').strip().lower().replace('-', ' ').split())
    return kind if kind in AMENITY_KINDS else 'other'


def _amenities(value):
    out = []
    for raw in _list(value):
        raw = _dict(raw)
        name = _line(raw.get('name'), NAME)
        if not name:
            continue
        location_id = raw.get('location_id')
        if isinstance(location_id, bool) or not isinstance(location_id, int) or location_id <= 0:
            location_id = None
        entry = {'kind': amenity_kind(raw.get('kind')), 'name': name, 'location_id': location_id}
        note = _line(raw.get('note'), NOTE)
        if note:
            entry['note'] = note
        out.append(entry)
        if len(out) == MAX_AMENITIES:
            break
    return out


def _menu(value):
    out = []
    for raw in _list(value):
        raw = _dict(raw)
        name = _line(raw.get('name'), NAME)
        if not name:
            continue
        entry = {'name': name}
        for key, limit in (('price', PRICE), ('note', NOTE)):
            text = _line(raw.get(key), limit)
            if text:
                entry[key] = text
        out.append(entry)
        if len(out) == MAX_MENU:
            break
    return out


def genres(value):
    out = []
    for raw in _list(value):
        genre = _line(raw, GENRE).lower().rstrip('…') if isinstance(raw, str) else ''
        if genre and genre not in out:
            out.append(genre)
        if len(out) == MAX_GENRES:
            break
    return out


def _performers(value):
    out = []
    for raw in _list(value):
        raw = _dict(raw)
        name = _line(raw.get('name'), NAME)
        if not name:
            continue
        entry = {'name': name}
        bio = _line(raw.get('bio'), BIO)
        if bio:
            entry['bio'] = bio
        tags = genres(raw.get('genres'))
        if tags:
            entry['genres'] = tags
        out.append(entry)
        if len(out) == MAX_PERFORMERS:
            break
    return out


_LISTS = {'faq': _faq, 'amenities': _amenities, 'menu': _menu, 'performers': _performers}


# ---------------------------------------------------------------------------
# Objects
# ---------------------------------------------------------------------------

def normalize(kind, raw):
    """The normalised context of an ``event`` / ``location`` / ``item`` as a dict (``{}`` when empty)."""
    raw = _dict(raw)
    texts = TEXTS[kind]
    out = {}
    for key in KEYS[kind]:
        if key == 'links':
            value = _links(raw.get('links'), MAX_LINKS[kind])
        elif key in _LISTS:
            value = _LISTS[key](raw.get(key))
        elif key == 'about':
            value = _block(raw.get(key), texts[key])
        else:
            value = _block(raw.get(key), texts[key]) if texts[key] > 300 else _line(raw.get(key), texts[key])
        if value:
            out[key] = value
    return out


def for_bundle(kind, raw, location_ids=None):
    """The context as sent to the apps: normalised, or ``None`` when empty. ``location_ids`` (event only): the
    visible locations; an amenity pointing anywhere else gets ``location_id: null``."""
    ctx = normalize(kind, raw)
    if location_ids is not None and ctx.get('amenities'):
        for entry in ctx['amenities']:
            if entry['location_id'] not in location_ids:
                entry['location_id'] = None
    return ctx or None


def size(ctx):
    return len(json.dumps(ctx, ensure_ascii=False, separators=(',', ':')).encode()) if ctx else 0


def apply_budget(event_ctx, locations, items, budget=BUNDLE_BUDGET_BYTES, label=''):
    """Keep a bundle's context within ``budget`` bytes. ``locations`` / ``items``: the bundle's serialised dicts
    (each with a ``context`` key), changed in place. Drops item ``about`` texts from the last item backwards, then
    location ``about`` texts likewise, then (only if still over) whole item / location contexts, last first."""
    total = size(event_ctx) + sum(size(o['context']) for o in locations) + sum(size(o['context']) for o in items)
    if total <= budget:
        return total
    dropped = 0
    steps = ([(o, 'about') for o in reversed(items)] + [(o, 'about') for o in reversed(locations)]
             + [(o, None) for o in reversed(items)] + [(o, None) for o in reversed(locations)])
    for obj, key in steps:
        if total <= budget:
            break
        ctx = obj['context']
        if not ctx or (key and key not in ctx):
            continue
        before = size(ctx)
        if key:
            ctx = {k: v for k, v in ctx.items() if k != key} or None
        else:
            ctx = None
        obj['context'] = ctx
        total += size(ctx) - before
        dropped += 1
    logger.warning('Bundle context over budget%s: dropped %d context parts, now %d bytes',
                   f' ({label})' if label else '', dropped, total)
    return total


# ---------------------------------------------------------------------------
# Review / console text
# ---------------------------------------------------------------------------

LABELS = {'about': 'About', 'faq': 'FAQ', 'amenities': 'Amenities', 'accessibility': 'Accessibility',
          'policies': 'Policies', 'getting_there': 'Getting there', 'weather_plan': 'Weather plan', 'links': 'Links',
          'menu': 'Menu', 'hours': 'Hours', 'age_limit': 'Age limit', 'performers': 'Performers', 'price': 'Price'}


def summary(kind, raw):
    """A short readable summary of a context ("About: … · FAQ: 6 · Links: 2"), '' when empty."""
    ctx = normalize_named(kind, raw)
    parts = []
    for key, value in ctx.items():
        if isinstance(value, list):
            parts.append(f'{LABELS[key]}: {len(value)}')
        else:
            parts.append(f'{LABELS[key]}: {cut(" ".join(value.split()), 60)}')
    return ' · '.join(parts)


def describe_changes(kind, old, new):
    """What changed between two contexts, for the review page ("FAQ +2 −1 · About rewritten · Hours added")."""
    old, new = normalize_named(kind, old), normalize_named(kind, new)
    parts = []
    for key in KEYS[kind]:
        a, b = old.get(key), new.get(key)
        if a == b:
            continue
        label = LABELS[key]
        if a is None:
            parts.append(f'{label} added')
        elif b is None:
            parts.append(f'{label} removed')
        elif isinstance(b, list):
            added = sum(1 for e in b if e not in a)
            removed = sum(1 for e in a if e not in b)
            bits = ([f'+{added}'] if added else []) + ([f'−{removed}'] if removed else [])
            parts.append(f'{label} {" ".join(bits)}' if bits else f'{label} reordered')
        else:
            parts.append(f'{label} changed')
    return ' · '.join(parts)


def merge(kind, old, new):
    """An AI view (``new``) merged onto the current context (``old``), both normalised the same way: keys the AI left
    empty keep their current value (empty AI values never blank data), and performers / menu entries the AI gives
    by the same name keep a bio, genres, price or note it left out."""
    old, new = normalize_named(kind, old), normalize_named(kind, new)
    out = {**old, **new}
    for key, fill in (('performers', ('bio', 'genres')), ('menu', ('price', 'note'))):
        if key in new and key in old:
            before = {' '.join(e['name'].lower().split()): e for e in old[key]}
            merged = []
            for entry in new[key]:
                prev = before.get(' '.join(entry['name'].lower().split()))
                if prev:
                    entry = {**{k: prev[k] for k in fill if k in prev}, **entry}
                merged.append(entry)
            out[key] = merged
    return normalize_named(kind, out)


# ---------------------------------------------------------------------------
# Amenity locations by name (AI import / refresh, console)
# ---------------------------------------------------------------------------
# The AI and the console name an amenity's location ("location_name"); storage and the bundle hold its id. The
# "named" form is the normalised context with each amenity's location_id replaced by location_name.

def normalize_named(kind, raw, names=None):
    """Like ``normalize``, but amenities carry ``location_name`` (from the raw entry, else ``names[location_id]``)
    instead of ``location_id``."""
    raw = _dict(raw)
    out = normalize(kind, {k: v for k, v in raw.items() if k != 'amenities'})
    if kind != EVENT:
        return out
    amenities = []
    for entry in _list(raw.get('amenities')):
        entry = _dict(entry)
        clean = _amenities([entry])
        if not clean:
            continue
        clean = clean[0]
        location_id = clean.pop('location_id')
        name = _line(entry.get('location_name'), NAME) or (names or {}).get(location_id) or None
        named = {'kind': clean['kind'], 'name': clean['name'], 'location_name': name}
        if 'note' in clean:
            named['note'] = clean['note']
        amenities.append(named)
        if len(amenities) == MAX_AMENITIES:
            break
    if amenities:
        out['amenities'] = amenities
        out = {k: out[k] for k in EVENT_KEYS if k in out}
    return out


def resolve_named(kind, named, find_location):
    """The storable context from the named form: ``find_location(name)`` → a location id or None."""
    named = _dict(named)
    if kind != EVENT or not named.get('amenities'):
        return normalize(kind, named)
    amenities = []
    for entry in _list(named.get('amenities')):
        entry = _dict(entry)
        name = entry.get('location_name')
        amenities.append({**entry, 'location_id': find_location(name) if name else entry.get('location_id')})
    return normalize(kind, {**named, 'amenities': amenities})
