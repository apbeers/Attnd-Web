"""
Display configuration: how the mobile apps lay out an event.

Organizers store only the settings they override in ``Event.display_config``.
``resolve_display(event)`` fills every key, choosing a preset automatically
from the size and spread of the event when ``preset`` is ``auto``.

The resolved dict is sent to the apps in the v2 bundle. Clients must ignore
unknown keys and fall back to their own defaults for missing ones, so new
settings can be added without breaking older app versions.
"""

from math import asin, cos, radians, sin, sqrt

SCHEMA_VERSION = 1

PRESETS = ['auto', 'intimate', 'standard', 'festival']
# 'home' (API-024): a tab made of the `home` layout's sections; only for apps that send X-Attnd-Sections ≥ 1,
# never in the automatic tabs (organizers turn it on).
TABS = ['home', 'map', 'locations', 'schedule', 'flyer', 'notifications']
MAX_TABS = 5
LOCATIONS_STYLES = ['cards', 'list', 'grouped']
SCHEDULE_STYLES = ['timeline', 'by_day', 'by_location', 'now_next']
SECONDARY_PANES = ['locations', 'schedule', 'now_next', 'none']

# Human-readable labels for the console
LABELS = {
    'preset': {
        'auto': 'Automatic (based on event size)',
        'intimate': 'Intimate — a handful of spots, big visual cards',
        'standard': 'Standard — balanced map, list and schedule',
        'festival': 'Festival — lots of things, search and filters first',
    },
    'locations_style': {
        'cards': 'Large cards with photos',
        'list': 'Compact list',
        'grouped': 'Grouped by category',
    },
    'schedule_style': {
        'timeline': 'Timeline',
        'by_day': 'Grouped by day',
        'by_location': 'Grouped by location',
        'now_next': 'Now / Next',
    },
    'secondary_pane': {
        'locations': 'Location list',
        'schedule': 'Schedule',
        'now_next': 'Now / Next',
        'none': 'Nothing (map fills the screen)',
    },
    'tabs': {
        'home': 'Home',
        'map': 'Map',
        'locations': 'Locations',
        'schedule': 'Schedule',
        'flyer': 'Flyer',
        'notifications': 'Updates',
    },
}

PRESET_DEFAULTS = {
    'intimate': {
        'tabs': ['map', 'locations', 'schedule', 'flyer', 'notifications'],
        'default_tab': 'map',
        'locations_style': 'cards',
        'schedule_style': 'timeline',
        'map_clustering': False,
        'map_fit_all': True,
        'show_location_images': True,
        'enable_search': False,
        'group_by_category': False,
        'secondary_pane': 'locations',
    },
    'standard': {
        'tabs': ['map', 'locations', 'schedule', 'flyer', 'notifications'],
        'default_tab': 'map',
        'locations_style': 'list',
        'schedule_style': 'by_day',
        'map_clustering': False,
        'map_fit_all': True,
        'show_location_images': True,
        'enable_search': True,
        'group_by_category': False,
        'secondary_pane': 'now_next',
    },
    'festival': {
        'tabs': ['schedule', 'map', 'locations', 'flyer', 'notifications'],
        'default_tab': 'schedule',
        'locations_style': 'grouped',
        'schedule_style': 'now_next',
        'map_clustering': True,
        'map_fit_all': False,
        'show_location_images': False,
        'enable_search': True,
        'group_by_category': True,
        'secondary_pane': 'schedule',
    },
}

OVERRIDABLE_KEYS = [
    'preset', 'tabs', 'default_tab', 'locations_style', 'schedule_style',
    'map_clustering', 'map_fit_all', 'show_location_images', 'enable_search',
    'group_by_category', 'secondary_pane',
]
BOOL_KEYS = ['map_clustering', 'map_fit_all', 'show_location_images',
             'enable_search', 'group_by_category']


def _haversine_km(a, b):
    lat1, lon1, lat2, lon2 = map(radians, (a[0], a[1], b[0], b[1]))
    h = sin((lat2 - lat1) / 2) ** 2 + cos(lat1) * cos(lat2) * sin((lon2 - lon1) / 2) ** 2
    return 2 * 6371 * asin(sqrt(h))


def spread_km(points):
    """Largest distance between any two points (O(n²) is fine for event sizes)."""
    pts = [(float(lat), float(lng)) for lat, lng in points if lat is not None and lng is not None]
    best = 0.0
    for i in range(len(pts)):
        for j in range(i + 1, len(pts)):
            best = max(best, _haversine_km(pts[i], pts[j]))
    return round(best, 2)


def event_stats(event, locations=None):
    """`locations`: the event's visible locations when the caller has them already (saves a query)."""
    if locations is not None:
        locations = [(loc.latitude, loc.longitude, loc.category) for loc in locations]
    else:
        locations = list(event.location_set.filter(hidden=False).values_list('latitude', 'longitude', 'category'))
    item_count = event.scheduleditem_set.filter(hidden=False).count()
    categories = sorted({c for _, _, c in locations if c})
    return {
        'location_count': len(locations),
        'item_count': item_count,
        'spread_km': spread_km((lat, lng) for lat, lng, _ in locations),
        'category_count': len(categories),
        'has_flyer': bool(event.flyer),
    }


def auto_preset(stats):
    """Pick a preset from event size. Returns (preset, reason)."""
    locs, items = stats['location_count'], stats['item_count']
    if locs > 40 or items > 120:
        return 'festival', f'{locs} locations and {items} schedule items — a large event'
    if locs <= 12 and items <= 30:
        return 'intimate', f'{locs} locations and {items} schedule items — a small event'
    return 'standard', f'{locs} locations and {items} schedule items — a mid-sized event'


def clean_overrides(raw):
    """Validate organizer overrides; silently drops anything invalid."""
    raw = raw or {}
    out = {}
    if raw.get('preset') in PRESETS:
        out['preset'] = raw['preset']
    tabs = raw.get('tabs')
    if isinstance(tabs, list):
        tabs = [t for t in tabs if t in TABS]
        tabs = list(dict.fromkeys(tabs))  # dedupe, keep order
        if tabs:
            out['tabs'] = tabs
    if raw.get('default_tab') in TABS:
        out['default_tab'] = raw['default_tab']
    if raw.get('locations_style') in LOCATIONS_STYLES:
        out['locations_style'] = raw['locations_style']
    if raw.get('schedule_style') in SCHEDULE_STYLES:
        out['schedule_style'] = raw['schedule_style']
    if raw.get('secondary_pane') in SECONDARY_PANES:
        out['secondary_pane'] = raw['secondary_pane']
    for key in BOOL_KEYS:
        if isinstance(raw.get(key), bool):
            out[key] = raw[key]
    return out


def resolve_display(event, stats=None, sections=0):
    """`sections`: the layout catalogue version the app draws (X-Attnd-Sections, API-024); below 1 the Home tab
    is left out, so older apps never see it."""
    stats = stats or event_stats(event)
    overrides = clean_overrides(event.display_config)
    preset = overrides.get('preset', 'auto')
    if preset == 'auto':
        resolved_preset, reason = auto_preset(stats)
        # Tight venue with lots of locations: lead with the list, not the map
        if resolved_preset == 'standard' and stats['spread_km'] < 0.3 and stats['location_count'] > 20:
            reason += ', packed into a small area'
    else:
        resolved_preset, reason = preset, 'Chosen by the organizer'

    cfg = dict(PRESET_DEFAULTS[resolved_preset])

    # Size-sensitive tweaks applied to the auto-chosen defaults
    if stats['location_count'] > 20:
        cfg['map_clustering'] = True
    if stats['spread_km'] < 0.3 and stats['location_count'] > 20 and resolved_preset != 'festival':
        cfg['default_tab'] = 'locations'
    if stats['category_count'] >= 3 and stats['location_count'] > 12:
        cfg['group_by_category'] = True
    if stats['item_count'] == 0:
        cfg['tabs'] = [t for t in cfg['tabs'] if t != 'schedule']
        if cfg['secondary_pane'] in ('schedule', 'now_next'):
            cfg['secondary_pane'] = 'locations'

    cfg.update({k: v for k, v in overrides.items() if k != 'preset'})

    # Flyer tab only makes sense with a flyer
    if not stats['has_flyer']:
        cfg['tabs'] = [t for t in cfg['tabs'] if t != 'flyer']
    if not sections or sections < 1:
        cfg['tabs'] = [t for t in cfg['tabs'] if t != 'home']
    cfg['tabs'] = cfg['tabs'][:MAX_TABS]  # the apps show at most five (never a "More" tab)
    if not cfg['tabs']:
        cfg['tabs'] = ['map', 'notifications']
    if cfg['default_tab'] not in cfg['tabs']:
        cfg['default_tab'] = cfg['tabs'][0]

    cfg.update({
        'schema_version': SCHEMA_VERSION,
        'preset': preset,
        'resolved_preset': resolved_preset,
        'reason': reason,
        'accent_color': event.accent_color or None,
        'logo_url': _logo(event),
        'stats': stats,
    })
    return cfg


def _logo(event):
    """Our self-hosted copy of the logo when there is one, at the apps' logo width (attnd/images.py)."""
    from attnd.images import event_logo_url
    from events.content import app_image_url
    return app_image_url(event_logo_url(event), 'logo') or None
