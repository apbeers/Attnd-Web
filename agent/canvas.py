"""The full-screen canvas drawn on the server (A21): the event's tabs, their sections and cards and the active tab are
in the first HTML, so a reload paints them at once (no tab flashing, no layout shift); agent.js adopts them.

The structure is exactly what agent.js builds for sections it adds later (toCanvas / fillSection / makeCard).
"""

import json

from django.http import Http404

from console import components as C

# Tab of each component, the tab order and the section order inside a tab (agent.js has the same lists).
KINDS = {
    'event_tile': 'Events', 'event_overview': 'Overview', 'event_readiness': 'Overview', 'event_lifecycle': 'Overview', 'sharing_kit': 'Overview',
    'event_exports': 'Overview', 'event_rollover': 'Overview', 'changeset_review': 'Review', 'location_row': 'Locations',
    'schedule_item_row': 'Schedule', 'notification_form': 'Notifications', 'notification_row': 'Notifications',
    'event_team': 'Team', 'event_metrics': 'Metrics', 'usage_answer': 'Metrics', 'app_layout': 'Settings', 'website_watching': 'Settings',
    'event_context_form': 'Settings', 'event_flyer': 'Settings', 'object_history': 'History', 'event_theme': 'Overview',
    # Every admin tool is one Platform tab (owner, Nov 22, 2026: organizers and staff see the same tabs but this one).
    'event_versions': 'History', 'prospect_run_row': 'Platform', 'prospect_row': 'Platform',
    'ai_spend_summary': 'Platform', 'platform_health': 'Platform',
    'website_overview': 'Website', 'website_versions': 'Website', 'website_assets': 'Website', 'website_forms': 'Website',
}
# Tab counts that wait for the person's action are drawn as a filled alert badge (.tab-count--alert); others muted.
ALERT_COUNTS = {'Review'}
ORDER = ['Events', 'Overview', 'Review', 'Locations', 'Schedule', 'Notifications', 'Team', 'Metrics', 'Settings',
         'Website', 'History', 'Platform']
# The Website tab (Attnd Studio inside Ask Attnd): only for people with Studio access.
WEBSITE_SECTIONS = ['website_overview', 'website_versions', 'website_forms', 'website_assets']
EVENT_SECTIONS = ['event_overview', 'event_readiness', 'event_lifecycle', 'sharing_kit', 'event_exports', 'event_rollover',
                  'changeset_review', 'location_row', 'schedule_item_row', 'notification_form', 'notification_row', 'event_team',
                  'event_metrics', 'usage_answer', 'app_layout', 'website_watching', 'event_context_form', 'event_flyer',
                  'object_history', 'event_versions']
EMPTY = {'location_row': 'No locations yet. Add stages, booths, food, anywhere attendees go.',
         'schedule_item_row': 'No schedule items yet.', 'notification_row': 'No notifications sent or scheduled yet.',
         'event_tile': 'No events yet.', 'prospect_row': 'No open prospects. Run a discovery from its settings.',
         'prospect_run_row': 'No discovery runs yet.'}
HEADINGS = {'prospect_run_row': 'Prospects · latest runs', 'prospect_row': 'Open prospects, best fit first',
            'ai_spend_summary': 'AI spend', 'platform_health': 'Health'}
GROUPS = {'scheduled': 'Scheduled', 'sent': 'Sent', 'now': 'Happening now', 'upcoming': 'Upcoming', 'past': 'Past',
          'showcase': 'Showcase & demo'}
# Every section starts open (owner, Nov 21, 2026); the toggle folds one by hand, never remembered. Past shows 6 first.
FOLDED = set()
PAST_SHOWN = 6


def _cards(request, name, parent):
    from agent.components import RENDER_LIMIT, rendered
    try:
        found, total = C.instances_page(request.user, name, [parent], 0, RENDER_LIMIT)
    except (Http404, C.IdError):
        return [], 0
    return rendered(request, name, found), total


def _section(request, name, parent):
    items, total = _cards(request, name, parent)
    groups = {}
    for item in items:
        if item.get('group'):
            groups[item['group']] = groups.get(item['group'], 0) + 1
    # Grouped lists (Your events, notifications): one block per group (its heading, then its own cards), never
    # headings and cards side by side in one grid.
    rows, blocks, seen = [], [], {}
    for item in items:
        group = item.get('group')
        seen[group] = seen.get(group, 0) + 1
        row = {'card': item, 'ids': json.dumps(item['ids'], separators=(',', ':')), 'group': group or '',
               'more': group == 'past' and seen[group] > PAST_SHOWN,
               'detail_ids': json.dumps(item['detail_ids'], separators=(',', ':'))}
        if group and group in GROUPS:
            if not blocks or blocks[-1]['group'] != group:
                blocks.append({'group': group, 'heading': f'{GROUPS[group]} · {groups[group]}',
                               'folded': group in FOLDED, 'cards': []})
            blocks[-1]['cards'].append(row)
        else:
            rows.append(row)
    for b in blocks:
        b['more_past'] = seen.get('past', 0) if b['group'] == 'past' and seen.get('past', 0) > PAST_SHOWN else 0
    folded = ' '.join(g for g in FOLDED if g in groups)
    return {'name': name, 'parent': json.dumps(parent, separators=(',', ':')), 'rows': rows, 'blocks': blocks, 'items': items,
            'folded': folded,
            'next_offset': len(items) if len(items) < total else None, 'total': total,
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
        from studio.access import has_studio_access
        if has_studio_access(request.user):
            for name in WEBSITE_SECTIONS:
                add(name, {'event_id': event.pk})
    for name in ('platform_health', 'ai_spend_summary', 'prospect_run_row', 'prospect_row'):
        if C.allowed(C.get(name), request.user):  # attnd.permissions (the Staff / Platform admins roles)
            add(name, {})
    active = 'Overview' if event is not None else 'Events'
    wanted = (request.GET.get('tab') or '').capitalize()
    if wanted in tabs:  # ?tab=website (the Website page's "Change it with Ask Attnd")
        active = wanted
    out = []
    for label in sorted(tabs, key=ORDER.index):
        count, alert = None, False
        if label == 'Review':  # AI suggestions wait for the person: an alert badge
            count, alert = len(tabs[label][0]['items']), True
        out.append({'label': label, 'text': label, 'count': count, 'alert': alert, 'id': f'canvas-{label.lower().replace(" ", "-")}',
                    'active': label == active, 'sections': tabs[label]})
    return out


# Console pages the canvas (and the agent) can show as components: path → (component, ids, tab). agent.js gets this
# list from the page (data-routes) instead of keeping a copy.
ROUTES = [
    (r'^/console/events/(?P<event_id>\d+)/locations/new/$', 'location_form', None),
    (r'^/console/events/(?P<event_id>\d+)/locations/(?P<location_id>\d+)/$', 'location_form', None),
    (r'^/console/events/(?P<event_id>\d+)/schedule/new/$', 'schedule_item_form', None),
    (r'^/console/events/(?P<event_id>\d+)/schedule/(?P<item_id>\d+)/$', 'schedule_item_form', None),
    (r'^/console/events/(?P<event_id>\d+)/edit/$', 'event_form', None),
    (r'^/console/events/(?P<event_id>\d+)/history/$', 'object_history', 'History'),
    (r'^/console/events/(?P<event_id>\d+)/locations/$', 'location_row', 'Locations'),
    (r'^/console/events/(?P<event_id>\d+)/schedule/$', 'schedule_item_row', 'Schedule'),
    (r'^/console/events/(?P<event_id>\d+)/notifications/$', 'notification_row', 'Notifications'),
    (r'^/console/events/(?P<event_id>\d+)/team/$', 'event_team', 'Team'),
    (r'^/console/events/(?P<event_id>\d+)/usage/$', 'event_metrics', 'Metrics'),
    (r'^/console/events/(?P<event_id>\d+)/display/$', 'app_layout', 'Settings'),
    (r'^/console/events/(?P<event_id>\d+)/theme/$', 'event_theme_editor', None),
    (r'^/console/events/(?P<event_id>\d+)/changes/$', 'changeset_review', 'Review'),
    (r'^/console/events/(?P<event_id>\d+)/$', 'event_overview', 'Overview'),
    (r'^/console/new/$', 'event_new_form', None),
    (r'^/console/$', 'event_tile', 'Events'),
]


def match(path):
    """(component, ids, tab) for a console path the components can show, else None."""
    import re
    for pattern, name, tab in ROUTES:
        m = re.match(pattern, path or '')
        if m:
            return name, {k: int(v) for k, v in m.groupdict().items()}, tab
    return None


def routes_json():
    """For agent.js: [[pattern, component, tab], …] (JavaScript reads the named groups the same way)."""
    return json.dumps([[p.replace('(?P<', '(?<'), name, tab] for p, name, tab in ROUTES])
