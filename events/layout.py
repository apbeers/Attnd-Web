"""
Screen layouts (API-024): which sections the apps draw on each screen, in which order.

The bundle's ``layout`` lists, per screen, sections from one closed catalogue (``CATALOGUE``):
``{type, show?, title?, limit?, when?}``, plus ``overrides`` that replace a detail screen's sections
for places of one category or items with one tag. The apps draw only types compiled into them and
skip the rest; a missing or invalid layout falls back to their built-in one (today's screens).

- ``automatic(event, locations, items)`` builds the layout from what the event actually has:
  sections whose data no place / item / the event has are left out, food places with menus put
  the menu first, tags whose items have performers put the performers first, and the Home screen
  depends on the event's preset (festival / standard / intimate).
- ``clean_config(raw)`` validates what organizers store in ``Event.layout_config`` (the console's
  Screens editor, Ask Attnd's ``update_screen_layout``); anything invalid is dropped silently.
- ``resolve_layout(event, locations, items, sections)``: the organizer's screens over the
  automatic ones, filtered to the catalogue version the app sent (``X-Attnd-Sections``), or
  ``None`` when it sent none (older apps never see a layout, so they never show gaps).

The iOS rules (``Attnd/ScreenLayout.swift``) mirror ``CATALOGUE``, ``DEFAULTS`` and the conditions.
"""

import re

CATALOGUE_VERSION = 1
MAX_SECTIONS = 24
MAX_OVERRIDES = 8
MAX_TITLE = 60
MAX_LIMIT = 20

SCREENS = ['home', 'event_info', 'item_detail', 'place_detail', 'map_overlay', 'options_event']

SCREEN_LABELS = {
    'home': 'Home tab',
    'event_info': 'Event info',
    'item_detail': 'Schedule item',
    'place_detail': 'Place',
    'map_overlay': 'Map',
    'options_event': 'Options',
}

ITEM_HEADER_PARTS = ['time', 'status', 'tags']
PLACE_HEADER_PARTS = ['image', 'category', 'tags', 'address', 'distance', 'now_next']


def _t(label, since=1, needs=(), parts=(), help=''):
    return {'label': label, 'since': since, 'needs': list(needs), 'parts': list(parts), 'help': help}


# Per screen: type → label, the catalogue version that added it, the facts it needs (any one; the
# apps check them per subject), its parts. Keep in step with SectionCatalogue in the iOS app.
CATALOGUE = {
    'item_detail': {
        'header': _t('Name, time and status', parts=ITEM_HEADER_PARTS),
        'about': _t('About', needs=['description', 'context.about']),
        'performers': _t('Performers', needs=['context.performers']),
        'good_to_know': _t('Good to know (price, ages, accessibility)',
                           needs=['context.price', 'context.age_limit', 'context.accessibility']),
        'links': _t('Links', needs=['context.links']),
        'place_link': _t('Where it is'),
        'countdown': _t('Countdown to the start', needs=['upcoming']),
        'ask_about': _t('Ask about this (on-device AI)', needs=['model_available']),
    },
    'place_detail': {
        'header': _t('Photo, name and details', parts=PLACE_HEADER_PARTS),
        'walk_map': _t('Walking map and directions', needs=['coordinate']),
        'directions': _t('Directions button', needs=['coordinate']),
        'ask_about': _t('Ask about this (on-device AI)', needs=['model_available']),
        'about': _t('About', needs=['description', 'context.about']),
        'hours': _t('Opening hours', needs=['context.hours']),
        'good_to_know': _t('Good to know (ages, accessibility)', needs=['context.age_limit', 'context.accessibility']),
        'menu': _t('Menu', needs=['context.menu']),
        'links': _t('Links and website', needs=['context.links', 'website']),
        'now_next': _t('Now and next here', needs=['items_here']),
        'at_this_location': _t('Everything at this place', needs=['items_here']),
    },
    'event_info': {
        'header': _t('Event header', parts=['website']),
        'about': _t('About', needs=['description', 'context.about']),
        'faq': _t('FAQ', needs=['context.faq']),
        'amenities': _t('Amenities', needs=['context.amenities']),
        'getting_there': _t('Getting there', needs=['context.getting_there']),
        'accessibility': _t('Accessibility', needs=['context.accessibility']),
        'policies': _t('Policies', needs=['context.policies']),
        'weather_plan': _t('Weather plan', needs=['context.weather_plan']),
        'links': _t('Links', needs=['context.links']),
    },
    'options_event': {
        'header': _t('Event header'),
        'website': _t('Website button', needs=['website']),
        'event_info_link': _t('FAQ, amenities & getting there', needs=['context']),
    },
    'map_overlay': {
        'now_playing': _t('Now playing strip', needs=['now_playing']),
        'selected_place': _t('Card of the tapped pin'),
    },
    'home': {
        'header': _t('Event header'),
        'urgent': _t('Urgent announcement', needs=['urgent']),
        'countdown': _t('Countdown to the start', needs=['before_start']),
        'now_next': _t('Now and next', needs=['schedule']),
        'plan_today': _t('Your plan today (on-device AI)', needs=['plan']),
        'liked': _t('Liked things', needs=['likes']),
        'whats_on': _t("What's on", needs=['schedule']),
        'map': _t('Map preview', needs=['pins']),
        'places': _t('Places', needs=['locations']),
        'updates': _t('Latest updates', needs=['updates']),
        'faq': _t('FAQ highlights', needs=['context.faq']),
        'getting_there': _t('Getting there', needs=['context.getting_there']),
        'about': _t('About', needs=['description', 'context.about']),
        'links': _t('Links', needs=['context.links']),
        'flyer': _t('Flyer', needs=['flyer']),
        'website': _t('Website button', needs=['website']),
    },
}

FACTS = {
    'description', 'image', 'coordinate', 'website', 'tags', 'category', 'flyer', 'logo',
    'context', 'context.about', 'context.menu', 'context.hours', 'context.accessibility', 'context.age_limit',
    'context.price', 'context.performers', 'context.links', 'context.faq', 'context.amenities',
    'context.getting_there', 'context.policies', 'context.weather_plan',
    'items_here', 'likes', 'plan', 'updates', 'urgent', 'locations', 'pins', 'schedule', 'upcoming',
}
STATES = {'model_available', 'now_playing', 'within_walk', 'before_start', 'during', 'after_end'}
CONDITION_LABELS = {
    'before_start': 'Before the event starts', 'during': 'While the event is on', 'after_end': 'After the event',
    'not:after_end': 'Until the event ends', 'has_plan': 'When the attendee has a plan',
    'model_available': 'With on-device AI', 'now_playing': 'While something is on',
    'within_walk': 'Within walking distance',
}

# Sections drawn whatever the data (the apps also draw them every time).
_ALWAYS = {'header', 'place_link', 'ask_about', 'selected_place', 'plan_today', 'liked', 'urgent', 'now_playing',
           'countdown'}


def _s(type_, **kw):
    out = {'type': type_}
    out.update({k: v for k, v in kw.items() if v not in (None, [], '')})
    return out


# The apps' built-in layouts: today's screens (the iOS defaults are the same lists).
DEFAULTS = {
    'item_detail': [_s('header', show=ITEM_HEADER_PARTS), _s('about'), _s('performers'), _s('good_to_know'),
                    _s('links'), _s('place_link'), _s('ask_about')],
    'place_detail': [_s('header', show=PLACE_HEADER_PARTS), _s('walk_map'), _s('ask_about'), _s('about'), _s('hours'),
                     _s('good_to_know'), _s('menu'), _s('links'), _s('at_this_location')],
    'event_info': [_s('header', show=['website']), _s('about'), _s('faq'), _s('amenities'), _s('getting_there'),
                   _s('accessibility'), _s('policies'), _s('weather_plan'), _s('links')],
    'options_event': [_s('header'), _s('website'), _s('event_info_link')],
    'map_overlay': [_s('now_playing'), _s('selected_place')],
    'home': [_s('header'), _s('urgent'), _s('countdown'), _s('now_next', when=['not:after_end']), _s('plan_today'),
             _s('liked'), _s('map'), _s('updates', limit=3), _s('faq', limit=3), _s('getting_there'), _s('about')],
}

HOME_BY_PRESET = {
    'festival': [_s('header'), _s('urgent'), _s('countdown'), _s('now_next', when=['not:after_end']),
                 _s('plan_today'), _s('liked'), _s('whats_on', limit=6), _s('map'), _s('updates', limit=3),
                 _s('faq', limit=3), _s('getting_there')],
    'standard': [_s('header'), _s('urgent'), _s('countdown'), _s('now_next', when=['not:after_end']),
                 _s('plan_today'), _s('liked'), _s('map'), _s('whats_on', limit=6), _s('places', limit=8),
                 _s('updates', limit=3), _s('faq', limit=3), _s('getting_there'), _s('about')],
    'intimate': [_s('header'), _s('urgent'), _s('countdown'), _s('whats_on', limit=12, title='Agenda'),
                 _s('places', limit=8), _s('about'), _s('links'), _s('faq', limit=3), _s('getting_there')],
}

FOOD_WORDS = ('food', 'drink', 'restaurant', 'cafe', 'café', 'bar', 'truck', 'kitchen', 'eat')


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

_DATE = re.compile(r'^\d{4}-\d{2}-\d{2}$')


def valid_condition(c):
    if not isinstance(c, str):
        return False
    c = c.strip().lower()
    if c.startswith('not:'):
        return valid_condition(c[4:])
    if c == 'has_plan' or c in STATES:
        return True
    if c.startswith('has:'):
        return c[4:] in FACTS
    if c.startswith('day:'):
        return bool(_DATE.match(c[4:]))
    return False


def clean_section(raw, screen):
    """One section, or None. Unknown types for the screen, parts, titles and limits are cleaned like the apps do."""
    if not isinstance(raw, dict):
        return None
    name = str(raw.get('type') or '').strip().lower()
    spec = CATALOGUE.get(screen, {}).get(name)
    if spec is None:
        return None
    out = {'type': name}
    show = raw.get('show')
    if isinstance(show, list) and spec['parts']:
        parts = list(dict.fromkeys(str(p).lower() for p in show if str(p).lower() in spec['parts']))
        if parts:
            out['show'] = parts
    title = raw.get('title')
    if isinstance(title, str) and title.strip():
        title = ' '.join(title.split())
        out['title'] = title if len(title) <= MAX_TITLE else title[:MAX_TITLE - 1] + '…'
    limit = raw.get('limit')
    if isinstance(limit, int) and not isinstance(limit, bool):
        out['limit'] = min(max(limit, 1), MAX_LIMIT)
    when = raw.get('when')
    if isinstance(when, str):
        when = [when]
    if isinstance(when, list):
        conds = list(dict.fromkeys(c.strip().lower() for c in when if valid_condition(c)))
        if conds:
            out['when'] = conds
    return out


def clean_sections(raw, screen):
    out, seen = [], set()
    for section in raw if isinstance(raw, list) else []:
        s = clean_section(section, screen)
        if s and s['type'] not in seen:
            seen.add(s['type'])
            out.append(s)
            if len(out) == MAX_SECTIONS:
                break
    return out


def clean_match(raw):
    if not isinstance(raw, str) or ':' not in raw:
        return None
    kind, value = raw.split(':', 1)
    kind, value = kind.strip().lower(), ' '.join(value.split())
    if kind not in ('category', 'tag') or not value:
        return None
    return f'{kind}:{value.lower() if kind == "tag" else value}'[:80]


def clean_screen(raw, screen):
    if not isinstance(raw, dict):
        return None
    sections = clean_sections(raw.get('sections'), screen)
    if not sections:
        return None
    out = {'sections': sections}
    if screen in ('item_detail', 'place_detail'):
        overrides, seen = [], set()
        for o in raw.get('overrides') or []:
            if not isinstance(o, dict):
                continue
            match = clean_match(o.get('match'))
            secs = clean_sections(o.get('sections'), screen)
            if match and secs and match.lower() not in seen:
                seen.add(match.lower())
                overrides.append({'match': match, 'sections': secs})
            if len(overrides) == MAX_OVERRIDES:
                break
        if overrides:
            out['overrides'] = overrides
    return out


def clean_config(raw):
    """What organizers store in Event.layout_config: ``{"screens": {screen: {sections, overrides?}}}``."""
    screens = {}
    raw_screens = raw.get('screens') if isinstance(raw, dict) else None
    if not isinstance(raw_screens, dict):
        raw_screens = {}
    for screen in SCREENS:
        spec = clean_screen(raw_screens.get(screen), screen)
        if spec:
            screens[screen] = spec
    return {'screens': screens} if screens else {}


# ---------------------------------------------------------------------------
# Automatic layouts from the data
# ---------------------------------------------------------------------------

def _filled(value):
    return bool(value) and value not in ({}, [])


def data_facts(event, locations, items, website_url=None):
    """Which facts any place, item or the event has (for leaving out sections nothing could draw)."""
    ec = event.context or {}
    event_facts = {f'context.{k}' for k in ('about', 'faq', 'amenities', 'getting_there', 'accessibility', 'policies',
                                              'weather_plan', 'links') if _filled(ec.get(k))}
    if event_facts:
        event_facts.add('context')
    if event.description:
        event_facts.add('description')
    if website_url:
        event_facts.add('website')
    if event.flyer:
        event_facts.add('flyer')
    if items:
        event_facts.add('schedule')
    if locations:
        event_facts.add('locations')
    if any(loc.latitude is not None and loc.longitude is not None for loc in locations):
        event_facts.add('pins')
    place_facts, item_facts = set(), set()
    located = {i.location_id for i in items if i.location_id}
    for loc in locations:
        c = loc.context or {}
        place_facts |= {f'context.{k}' for k in ('about', 'menu', 'hours', 'accessibility', 'age_limit', 'links')
                        if _filled(c.get(k))}
        if loc.description:
            place_facts.add('description')
        if loc.latitude is not None and loc.longitude is not None:
            place_facts.add('coordinate')
        if getattr(loc, 'website', ''):
            place_facts.add('website')
        if loc.id in located:
            place_facts.add('items_here')
    for item in items:
        c = item.context or {}
        item_facts |= {f'context.{k}' for k in ('about', 'performers', 'price', 'age_limit', 'accessibility', 'links')
                       if _filled(c.get(k))}
        if item.description:
            item_facts.add('description')
        item_facts.add('upcoming')
    return {'event': event_facts, 'place': place_facts, 'item': item_facts}


def _keep(sections, screen, facts):
    """Leave out sections whose data nothing in the event has (the apps would skip them anyway)."""
    out = []
    for s in sections:
        needs = CATALOGUE[screen][s['type']]['needs']
        if s['type'] in _ALWAYS or not needs or any(n in facts for n in needs):
            out.append(dict(s))
    return out


def _is_food(text):
    text = (text or '').lower()
    return any(w in text for w in FOOD_WORDS)


def automatic(event, locations, items, preset='standard', website_url=None):
    """The automatic layout for every screen."""
    facts = data_facts(event, locations, items, website_url)
    screens = {}
    for screen in ('event_info', 'options_event', 'map_overlay'):
        screens[screen] = {'sections': _keep(DEFAULTS[screen], screen, facts['event'])}
    screens['item_detail'] = {'sections': _keep(DEFAULTS['item_detail'], 'item_detail', facts['item'])}
    screens['place_detail'] = {'sections': _keep(DEFAULTS['place_detail'], 'place_detail', facts['place'])}
    home = HOME_BY_PRESET.get(preset, HOME_BY_PRESET['standard'])
    if 'flyer' in facts['event']:
        home = home + [_s('flyer')]
    if 'website' in facts['event']:
        home = home + [_s('website')]
    screens['home'] = {'sections': _keep(home, 'home', facts['event'])}

    # Food places with a menu: the menu and hours first.
    food_categories = []
    for loc in locations:
        menu = (loc.context or {}).get('menu')
        if loc.category and _filled(menu) and (_is_food(loc.category) or any(_is_food(t) for t in loc.tags or [])):
            if loc.category not in food_categories:
                food_categories.append(loc.category)
    if food_categories:
        first = [_s('header', show=PLACE_HEADER_PARTS), _s('menu'), _s('hours')]
        rest = [s for s in screens['place_detail']['sections'] if s['type'] not in {'header', 'menu', 'hours'}]
        screens['place_detail']['overrides'] = [
            {'match': f'category:{cat}', 'sections': first + [dict(s) for s in rest]}
            for cat in food_categories[:MAX_OVERRIDES // 2]]

    # Tags whose items have performers: the performers first (the most used tags, ≤ 3).
    counts = {}
    for item in items:
        if _filled((item.context or {}).get('performers')):
            for tag in item.tags or []:
                counts[tag] = counts.get(tag, 0) + 1
    tags = [t for t, _ in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))][:3]
    if tags and 'context.performers' in facts['item']:
        base = screens['item_detail']['sections']
        first = [s for s in base if s['type'] == 'header'] + [_s('performers')]
        rest = [dict(s) for s in base if s['type'] not in {'header', 'performers'}]
        screens['item_detail']['overrides'] = [{'match': f'tag:{t}', 'sections': first + rest} for t in tags]
    return screens


def resolve_layout(event, locations, items, sections=0, preset='standard', website_url=None):
    """``bundle.layout`` for an app that draws catalogue version ``sections`` (None below version 1)."""
    if not sections or sections < 1:
        return None
    version = min(sections, CATALOGUE_VERSION)
    screens = automatic(event, locations, items, preset, website_url)
    source = {screen: 'automatic' for screen in screens}
    for screen, spec in (clean_config(event.layout_config).get('screens') or {}).items():
        screens[screen] = spec
        source[screen] = 'organizer'

    def allowed(secs, screen):
        return [s for s in secs if CATALOGUE[screen][s['type']]['since'] <= version]

    out = {}
    for screen in SCREENS:
        spec = screens.get(screen)
        if not spec:
            continue
        secs = allowed(spec['sections'], screen)
        if not secs:
            continue
        entry = {'sections': secs}
        overrides = [{'match': o['match'], 'sections': allowed(o['sections'], screen)}
                     for o in spec.get('overrides') or []]
        overrides = [o for o in overrides if o['sections']]
        if overrides:
            entry['overrides'] = overrides
        out[screen] = entry
    return {'catalogue': version, 'source': source, 'screens': out}


def sections_header(request):
    """The catalogue version an app draws, from ``X-Attnd-Sections`` (0 when missing or invalid)."""
    if request is None:
        return 0
    raw = (request.headers.get('X-Attnd-Sections') or '').strip()
    if not raw.isdigit():
        return 0
    return min(int(raw), 999)


# ---------------------------------------------------------------------------
# The console's Screens editor
# ---------------------------------------------------------------------------

def editor_screen(event, locations, items, screen, preset='standard', website_url=None):
    """One screen for the editor: every catalogue type, on (in order) then off, with labels and parts."""
    stored = (clean_config(event.layout_config).get('screens') or {}).get(screen)
    auto = automatic(event, locations, items, preset, website_url)[screen]
    spec = stored or auto
    on = spec['sections']
    on_types = [s['type'] for s in on]
    rows = []
    for s in on + [{'type': t} for t in CATALOGUE[screen] if t not in on_types]:
        cat = CATALOGUE[screen][s['type']]
        rows.append({
            'type': s['type'], 'label': cat['label'], 'on': s['type'] in on_types,
            'parts': [{'name': p, 'label': p.replace('_', ' ').capitalize(),
                       'on': p in (s.get('show') or cat['parts'])} for p in cat['parts']],
            'title': s.get('title', ''), 'limit': s.get('limit'),
            'when': [CONDITION_LABELS.get(c, c) for c in s.get('when') or []],
            'when_raw': s.get('when') or [],
        })
    return {'screen': screen, 'label': SCREEN_LABELS[screen], 'custom': bool(stored), 'rows': rows,
            'overrides': [{'match': o['match'], 'types': [x['type'] for x in o['sections']],
                           'labels': [CATALOGUE[screen][x['type']]['label'] for x in o['sections']]}
                          for o in spec.get('overrides') or []]}


# ---------------------------------------------------------------------------
# Preview (the console's phone-shaped preview: the apps' visibility rules on a sample subject)
# ---------------------------------------------------------------------------

def holds(condition, facts):
    """One condition, as the apps evaluate it (unknown → false, also when negated)."""
    c = (condition or '').strip().lower()
    if c.startswith('not:'):
        return valid_condition(c[4:]) and not holds(c[4:], facts)
    if c == 'has_plan':
        return 'plan' in facts
    if c.startswith('has:'):
        return c[4:] in FACTS and c[4:] in facts
    if c.startswith('day:'):
        return bool(_DATE.match(c[4:])) and c in facts
    return c in STATES and c in facts


def visible(screen, sections, facts):
    out = []
    for s in sections:
        needs = CATALOGUE[screen][s['type']]['needs']
        if needs and not any(n in facts for n in needs):
            continue
        if all(holds(c, facts) for c in s.get('when') or []):
            out.append(s)
    return out


def _ctx_facts(prefix_keys, ctx_value):
    c = ctx_value or {}
    facts = {f'context.{k}' for k in prefix_keys if _filled(c.get(k))}
    if facts:
        facts.add('context')
    return facts


def item_facts(item, location=None):
    facts = _ctx_facts(('about', 'performers', 'price', 'age_limit', 'accessibility', 'links'), item.context)
    if item.description:
        facts.add('description')
    if item.tags:
        facts.add('tags')
    facts.add('upcoming')
    if location is not None and location.latitude is not None and location.longitude is not None:
        facts.add('coordinate')
    return facts


def place_facts(location, items_here=False):
    facts = _ctx_facts(('about', 'menu', 'hours', 'accessibility', 'age_limit', 'links'), location.context)
    for fact, present in (('description', location.description), ('tags', location.tags),
                          ('category', location.category), ('image', location.image or location.image_url),
                          ('coordinate', location.latitude is not None and location.longitude is not None),
                          ('website', location.website), ('items_here', items_here)):
        if present:
            facts.add(fact)
    return facts


def event_facts(event, locations, items, website_url=None):
    facts = data_facts(event, locations, items, website_url)['event'] | {'updates'}
    return facts


# What the preview assumes about the attendee: the on-device AI, a plan, likes, the event on.
PREVIEW_STATE = {'model_available', 'plan', 'likes', 'during', 'now_playing', 'within_walk'}
