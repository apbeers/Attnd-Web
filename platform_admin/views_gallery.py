"""Platform → Components (batch 7 Track B): every console component of one event drawn three times: in a 360 px
frame as the chat / phone draws it, and at 768 and 1280 px as a page or the canvas draws it. Each frame is its own
width (the components adapt by container queries, not the window), so one page shows all three at once and the
phone check (scripts/ci/phone_check.cjs) flags anything that sticks out of its frame."""

import inspect

from django.http import Http404
from django.shortcuts import render

from agent.registry import agent_action
from attnd.permissions import requires
from console import components as C
from events.models import Event

FRAMES = [(360, 'chat'), (768, 'page'), (1280, 'page')]
SAMPLES = 2  # list components (rows) show their first two


def gallery_entries(user, event):
    out = []
    for name, comp in sorted(C.for_user(user).items()):
        needs = [k for k in comp.ids if k != 'event_id']
        if needs and not comp.expand:
            out.append({'name': name, 'title': comp.title, 'skipped': f'needs {", ".join(needs)}'})
            continue
        items = [{'event_id': event.pk}] if 'event_id' in comp.all_ids else [{}]
        try:
            if not comp.expand and 'event_id' in comp.all_ids and 'event' in inspect.signature(comp.data).parameters:
                # Like a page: hand the event already loaded and checked on (one query, not one per component).
                ids = C.clean_ids(comp, items[0])
                found = [(ids, C.load(user, name, ids, {'event': event}))]
            else:
                found = C.instances(user, name, items, limit=SAMPLES)
        except (C.IdError, Http404, KeyError, ValueError, TypeError) as error:
            out.append({'name': name, 'title': comp.title, 'skipped': type(error).__name__})
            continue
        if not found:
            out.append({'name': name, 'title': comp.title, 'skipped': 'nothing to show for this event'})
            continue
        out.append({'name': name, 'title': comp.title, 'found': found})
    return out


@agent_action('platform_components', 'The component gallery (staff): every console component of one event drawn at '
              '360 px (chat, phone) and 768 / 1280 px (page, canvas). ?event=<id>.', kind='read')
@requires('platform_admin.view_platformsettings')
def components(request):
    events = Event.objects.for_user(request.user, 'events.change_event').order_by('-start_time')[:50]
    chosen = request.GET.get('event', '')
    event = next((e for e in events if str(e.pk) == chosen), None) or (events[0] if events else None)
    entries = []
    if event is not None:
        for entry in gallery_entries(request.user, event):
            if 'found' in entry:
                found = entry.pop('found')
                entry['frames'] = []
                for width, surface in FRAMES:
                    html = [C.render(entry['name'], data, request, surface, uid=C.new_uid()) for _ids, data in found]
                    # Row components are list items (their page draws them in a <ul>): give them one here too.
                    entry['frames'].append({'width': width, 'surface': surface, 'html': html,
                                            'list': all(str(h).lstrip().startswith('<li') for h in html)})
            entries.append(entry)
    return render(request, 'platform_admin/components.html', {'events': events, 'event': event, 'entries': entries})
