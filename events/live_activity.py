"""The Live Activity's content choices per event (bundle `live_activity`, batch 6 screens item 5).

``{"follows": "my_schedule"|"everything"|"location", "location_id": 12|null, "buttons": ["add", "skip", "map"],
"urgent": true, "stale_text": "…"|null}``. Always in the bundle (the defaults when the organizer chose nothing). The
urgent block is a safety rule: always `true`, whatever is stored. Older apps ignore the key.
"""

FOLLOWS = {'my_schedule': 'My schedule (plan, else likes, else everything)', 'everything': "Everything on that day",
           'location': 'One place (a stage)'}
BUTTONS = {'add': 'Add to my schedule', 'skip': 'Skip', 'map': 'Map'}
MAX_STALE = 60
DEFAULT = {'follows': 'my_schedule', 'location_id': None, 'buttons': list(BUTTONS), 'urgent': True, 'stale_text': None}


def clean(raw, location_ids=None):
    """What is stored in Event.live_activity, cleaned like the apps do (anything invalid → the default)."""
    raw = raw if isinstance(raw, dict) else {}
    out = dict(DEFAULT, buttons=list(DEFAULT['buttons']))
    follows = raw.get('follows')
    location_id = raw.get('location_id')
    if follows == 'location':
        if isinstance(location_id, int) and not isinstance(location_id, bool) and (
                location_ids is None or location_id in location_ids):
            out['follows'], out['location_id'] = 'location', location_id
    elif follows in FOLLOWS:
        out['follows'] = follows
    buttons = raw.get('buttons')
    if isinstance(buttons, list):
        out['buttons'] = [b for b in BUTTONS if b in buttons]
    stale = raw.get('stale_text')
    if isinstance(stale, str) and stale.strip():
        stale = ' '.join(stale.split())
        out['stale_text'] = stale if len(stale) <= MAX_STALE else stale[:MAX_STALE - 1] + '…'
    out['urgent'] = True  # safety rule: urgent announcements always show
    return out


def for_bundle(event, locations):
    return clean(event.live_activity, {loc.id for loc in locations})


def stored(raw, location_ids=None):
    """Only what differs from the default ({} = automatic)."""
    data = clean(raw, location_ids)
    return {k: v for k, v in data.items() if k != 'urgent' and v != DEFAULT[k]}
