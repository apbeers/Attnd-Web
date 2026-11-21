"""The full-screen canvas drawn on the server (A21): the event's tabs, their sections and cards and the active tab are
in the first HTML, so a reload paints them at once (no tab flashing, no layout shift); agent.js adopts them.

The structure is exactly what agent.js builds for sections it adds later (toCanvas / fillSection / makeCard).
"""

import json

from django.http import Http404

from console import components as C

# Tab of each component, the tab order and the section order inside a tab (agent.js has the same lists).
KINDS = {
    'event_tile': 'Events', 'event_overview': 'Overview', 'event_lifecycle': 'Overview', 'sharing_kit': 'Overview',
    'event_exports': 'Overview', 'changeset_review': 'Review', 'location_row': 'Locations',
    'schedule_item_row': 'Schedule', 'notification_form': 'Notifications', 'notification_row': 'Notifications',
    'event_team': 'Team', 'event_metrics': 'Metrics', 'app_layout': 'Settings', 'website_watching': 'Settings',
    'event_context_form': 'Settings', 'event_flyer': 'Settings', 'object_history': 'History',
    'event_versions': 'History', 'prospect_run_row': 'Prospects', 'prospect_row': 'Prospects',
}
ORDER = ['Events', 'Overview', 'Review', 'Locations', 'Schedule', 'Notifications', 'Team', 'Metrics', 'Settings',
         'History', 'Prospects']
EVENT_SECTIONS = ['event_overview', 'event_lifecycle', 'sharing_kit', 'event_exports', 'changeset_review',
                  'location_row', 'schedule_item_row', 'notification_form', 'notification_row', 'event_team',
                  'event_metrics', 'app_layout', 'website_watching', 'event_context_form', 'event_flyer',
                  'object_history', 'event_versions']
EMPTY = {'location_row': 'No locations yet. Add stages, booths, food, anywhere attendees go.',
         'schedule_item_row': 'No schedule items yet.', 'notification_row': 'No notifications sent or scheduled yet.',
         'event_tile': 'No events yet.', 'prospect_row': 'No open prospects. Run a discovery from its settings.',
         'prospect_run_row': 'No discovery runs yet.'}
HEADINGS = {'prospect_run_row': 'Latest runs', 'prospect_row': 'Open prospects, best fit first'}
GROUPS = {'scheduled': 'Scheduled', 'sent': 'Sent'}


def _cards(request, name, parent):
    from agent.components import rendered
    try:
        found = C.instances(request.user, name, [parent])
    except (Http404, C.IdError):
        return []
    return rendered(request, name, found)


def _section(request, name, parent):
    items = _cards(request, name, parent)
    groups = {}
    for item in items:
        if item.get('group'):
            groups[item['group']] = groups.get(item['group'], 0) + 1
    rows, current = [], None
    for item in items:
        group = item.get('group')
        if group and group != current and group in GROUPS:
            current = group
            rows.append({'heading': f'{GROUPS[group]} · {groups[group]}'})
        rows.append({'card': item, 'ids': json.dumps(item['ids'], separators=(',', ':')),
                     'detail_ids': json.dumps(item['detail_ids'], separators=(',', ':'))})
    return {'name': name, 'parent': json.dumps(parent, separators=(',', ':')), 'rows': rows, 'items': items,
            'row_list': name.endswith('_row'), 'heading': HEADINGS.get(name, ''), 'empty': EMPTY.get(name, 'Nothing here yet.')}


def build(request, event=None):
    """[{label, id, active, sections}] in tab order; the active tab is the event's Overview (else Events)."""
    tabs = {}

    def add(name, parent):
        section = _section(request, name, parent)
        if name == 'changeset_review' and not section['items']:
            return  # the Review tab only while something waits
        tabs.setdefault(KINDS[name], []).append(section)

    add('event_tile', {})
    if event is not None:
        for name in EVENT_SECTIONS:
            add(name, {'event_id': event.pk})
    if request.user.is_staff or request.user.is_superuser:
        for name in ('prospect_run_row', 'prospect_row'):
            add(name, {})
    active = 'Overview' if event is not None else 'Events'
    out = []
    for label in sorted(tabs, key=ORDER.index):
        text = label
        if label == 'Review':
            text = f'Review · {len(tabs[label][0]["items"])}'
        out.append({'label': label, 'text': text, 'id': f'canvas-{label.lower()}', 'active': label == active,
                    'sections': tabs[label]})
    return out
