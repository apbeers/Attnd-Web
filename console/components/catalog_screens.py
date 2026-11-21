"""The Screens editor (API-024): App layout → Screens. Per screen of the apps (Home tab, event info, schedule item,
place, map, Options) the sections in order with on / off switches, the header's parts, titles and list sizes, the
per-category / per-tag orders of the detail screens, and a phone-shaped preview drawn by one small Django template per
section type (``components/screen/<type>.html``) from the same JSON the apps get. Saves as you change it
(``console:event_screens``, Ask Attnd ``update_screen_layout``); Undo and History like every console change.
"""

from django.urls import reverse
from django.utils import timezone

from console.components import component, form_json, get, resolve_actions
from console.components.catalog import _tz, editable_event

LIMITED = {'home': {'liked', 'whats_on', 'places', 'updates', 'faq', 'my_schedule'}, 'event_info': {'faq'},
           'schedule_tab': {'happening_now', 'up_next'}, 'places_tab': {'nearby'}, 'updates_tab': {'latest', 'faq'}}


def _subjects(event):
    locations = list(event.location_set.filter(hidden=False).order_by('sort_order', 'name'))
    items = list(event.scheduleditem_set.filter(hidden=False).select_related('location').order_by('start_time', 'name'))
    return locations, items


def _sample_place(locations, items, match=''):
    from events import layout as L
    here = {i.location_id for i in items}
    pool = locations
    if match.startswith('category:'):
        pool = [loc for loc in locations if (loc.category or '').lower() == match[9:].lower()] or locations
    elif match.startswith('tag:'):
        pool = [loc for loc in locations if match[4:] in (loc.tags or [])] or locations
    return max(pool, key=lambda loc: len(L.place_facts(loc, loc.id in here)), default=None)


def _sample_item(items, match=''):
    from events import layout as L
    pool = items
    if match.startswith('tag:'):
        pool = [i for i in items if match[4:] in (i.tags or [])] or items
    elif match.startswith('category:'):
        pool = [i for i in items if i.location and (i.location.category or '').lower() == match[9:].lower()] or items
    return max(pool, key=lambda i: len(L.item_facts(i, i.location)), default=None)


_CTX_TEXT = ('about', 'hours', 'accessibility', 'age_limit', 'price', 'getting_there', 'policies', 'weather_plan')


def _ctx(raw):
    """The context parts the preview templates read, every key present (filter arguments must exist)."""
    raw = raw if isinstance(raw, dict) else {}
    out = {k: str(raw.get(k) or '') for k in _CTX_TEXT}

    def rows(key, *fields):
        return [{f: str(r.get(f) or '') for f in fields} | ({'genres': [str(g) for g in r.get('genres') or []]}
                                                           if key == 'performers' else {})
                for r in raw.get(key) or [] if isinstance(r, dict)]
    out['menu'] = rows('menu', 'name', 'price')
    out['links'] = rows('links', 'title', 'url')
    out['amenities'] = rows('amenities', 'name')
    out['performers'] = rows('performers', 'name')
    out['faq'] = [{'q': str(r.get('q') or r.get('question') or '')} for r in raw.get('faq') or [] if isinstance(r, dict)]
    return out


def preview(event, screen, sections, locations, items, match='', website_url=None):
    """What the preview draws: the sample subject and the sections the apps would show for it."""
    from events import layout as L
    facts = set(L.PREVIEW_STATE)
    subject = {'kind': screen, 'name': event.name, 'when': '', 'tags': [], 'description': '', 'context': _ctx({}),
               'place': '', 'category': '', 'address': '', 'image': False, 'items': [], 'website': False,
               'places': [], 'updates': [], 'flyer': False}
    tz = _tz(event)
    if screen == 'item_detail':
        item = _sample_item(items, match)
        if item is None:
            return {'empty': 'Add a schedule item to preview this screen.', 'sections': [], 'subject': subject, 'hidden': []}
        facts |= L.item_facts(item, item.location)
        with timezone.override(tz):
            when = timezone.localtime(item.start_time).strftime('%a %-I:%M %p') if item.start_time else ''
        subject.update(name=item.name, when=when, tags=item.tags or [], description=item.description,
                       context=_ctx(item.context), place=item.location.name if item.location else 'General')
    elif screen == 'place_detail':
        loc = _sample_place(locations, items, match)
        if loc is None:
            return {'empty': 'Add a place to preview this screen.', 'sections': [], 'subject': subject, 'hidden': []}
        here = [i.name for i in items if i.location_id == loc.id]
        facts |= L.place_facts(loc, bool(here))
        subject.update(name=loc.name, category=loc.category or '', tags=loc.tags or [], address=loc.address,
                       description=loc.description, context=_ctx(loc.context), image=bool(loc.image or loc.image_url),
                       items=here[:3], website=bool(loc.website))
    else:
        facts |= L.event_facts(event, locations, items, website_url)
        ctx = event.context or {}
        subject.update(description=event.description, context=_ctx(ctx),
                       places=[loc.name for loc in locations[:4]], items=[i.name for i in items[:4]],
                       updates=list(event.notification_set.filter(sent=True).order_by('-send_time')
                                    .values_list('title', flat=True)[:3]),
                       website=bool(website_url), flyer=bool(event.flyer))
        if event.start_time:
            with timezone.override(tz):
                subject['when'] = timezone.localtime(event.start_time).strftime('%a %b %-d, %-I:%M %p')
    shown = L.visible(screen, sections, facts)
    if not subject['description']:
        subject['description'] = subject['context']['about']
    names = _pick_names(event, screen, locations, items)
    return {'subject': subject,
            'sections': [{'title': '', 'show': [], 'limit': None, **s, 'label': L.CATALOGUE[screen][s['type']]['label'],
                          'picked': [names[i] for i in s.get('ids') or [] if i in names]}
                         for s in shown],
            'hidden': [L.CATALOGUE[screen][s['type']]['label'] for s in sections if s not in shown]}


def _pick_names(event, screen, locations, items):
    """{id: name} of what a `featured` / `pinned` section of this screen picks from."""
    if screen == 'schedule_tab':
        return {i.id: i.name for i in items}
    if screen == 'places_tab':
        return {loc.id: loc.name for loc in locations}
    if screen == 'updates_tab':
        return dict(event.notification_set.filter(sent=True).order_by('-send_time').values_list('id', 'title')[:60])
    return {}


def _rows(screen, sections, choices=None):
    from events import layout as L
    on_types = [s['type'] for s in sections]
    rows = []
    for s in sections + [{'type': t} for t in L.CATALOGUE[screen] if t not in on_types]:
        cat = L.CATALOGUE[screen][s['type']]
        shown = s.get('show') or cat['parts']
        rows.append({
            'type': s['type'], 'label': cat['label'], 'on': s['type'] in on_types,
            'parts': [{'name': p, 'label': p.replace('_', ' ').capitalize(), 'on': p in shown} for p in cat['parts']],
            'title': s.get('title', ''), 'limit': s.get('limit') or '',
            'limited': s['type'] in LIMITED.get(screen, set()),
            'when': ', '.join(L.CONDITION_LABELS.get(c, c) for c in s.get('when') or []),
            # catalogue 2: what a featured / pinned section picks, and the welcome card's own text
            'choices': [{'id': k, 'name': v, 'on': k in (s.get('ids') or [])} for k, v in (choices or {}).items()]
            if s['type'] in L.IDS_TYPES else [],
            'welcome': s['type'] == 'welcome', 'text': s.get('text', ''), 'image': s.get('image', ''),
        })
    return rows


def screens_json(event, locations=None, items=None):
    from events import layout as L
    from events.content import _website_url
    from events.display import event_stats, resolve_display
    if locations is None:
        locations, items = _subjects(event)
    website = _website_url(event)
    preset = resolve_display(event, event_stats(event, locations), sections=L.CATALOGUE_VERSION)['resolved_preset']
    stored = L.clean_config(event.layout_config).get('screens') or {}
    display = resolve_display(event, event_stats(event, locations), sections=L.CATALOGUE_VERSION)
    auto = L.automatic(event, locations, items, preset, website, display)
    action = reverse('console:event_screens', args=[event.pk])
    comp = get('app_screens')
    out = []
    for screen in L.SCREENS:
        spec = stored.get(screen) or auto[screen]
        blocks = [{'match': '', 'label': 'Every ' + {'item_detail': 'schedule item', 'place_detail': 'place'}.get(screen, 'time'),
                   'rows': _rows(screen, spec['sections'], _pick_names(event, screen, locations, items)),
                   'preview': preview(event, screen, spec['sections'], locations, items, '', website)}]
        for o in spec.get('overrides') or []:
            kind, value = o['match'].split(':', 1)
            blocks.append({'match': o['match'], 'label': f'{"Places in" if kind == "category" else "Tagged"} “{value}”',
                           'rows': _rows(screen, o['sections']),
                           'preview': preview(event, screen, o['sections'], locations, items, o['match'], website),
                           'actions': resolve_actions(comp, {'event_id': event.pk}, [
                               {'name': 'remove_screen_override', 'label': 'Remove', 'style': 'ghost',
                                'params': {'screen': screen, 'match': o['match']}}])})
        matches = []
        if screen in ('item_detail', 'place_detail'):
            used = {o['match'].lower() for o in spec.get('overrides') or []}
            cats = sorted({loc.category for loc in locations if loc.category})
            tags = sorted({t for x in (items if screen == 'item_detail' else locations) for t in (x.tags or [])})
            matches = [m for m in [f'category:{c}' for c in cats] + [f'tag:{t}' for t in tags] if m.lower() not in used]
        out.append({
            'screen': screen, 'label': L.SCREEN_LABELS[screen], 'custom': screen in stored, 'blocks': blocks,
            'matches': [{'value': m, 'label': ('Places in ' if m.startswith('category:') else 'Tagged ') + m.split(':', 1)[1]}
                        for m in matches[:60]],
            'action': action,
            'actions': resolve_actions(comp, {'event_id': event.pk}, [
                {'name': 'reset_screen_layout', 'label': 'Reset to automatic', 'style': 'ghost',
                 'params': {'screen': screen}}]) if screen in stored else [],
        })
    return out


@component('app_screens', 'Screens', 'Which sections the apps show on each screen (Home tab, event info, schedule '
           'item, place, map, Options) and in which order: switches, header parts, titles, list sizes, orders for a '
           'category or tag, and a phone preview. Saves as you change it.',
           actions=(('reset_screen_layout', 'Reset to automatic'), ('remove_screen_override', 'Remove')),
           page=lambda ids: reverse('console:display', args=[ids['event_id']]) + '#screens')
def app_screens(user, event_id=None, event=None):
    from events.display import clean_overrides
    event = editable_event(user, event_id, event)
    tabs = clean_overrides(event.display_config).get('tabs') or []
    return {'event_id': event.pk, 'name': f'Screens · {event.name}', 'screens': screens_json(event),
            'home_on': 'home' in tabs, 'display_url': reverse('console:display', args=[event.pk])}


@component('live_activity_settings', 'Live Activity', "What the Lock Screen Live Activity follows by default (my "
           "schedule, everything, or one place), which buttons it shows, and its text when it can't update. Urgent "
           'announcements always show. Saves as you change it.', actions=(),
           page=lambda ids: reverse('console:display', args=[ids['event_id']]) + '#live-activity')
def live_activity_settings(user, event_id=None, event=None, form=None):
    from console.views_events import LiveActivityForm
    from events import live_activity as LA
    event = editable_event(user, event_id, event)
    current = LA.clean(event.live_activity)
    form = form or LiveActivityForm(event=event, initial={
        'follows': current['follows'], 'location_id': current['location_id'], 'buttons': current['buttons'],
        'stale_text': current['stale_text'] or ''})
    data = form_json(form, [('choices', 'follows'), ('show_if', 'follows:location', 'la-place', ['location_id']),
                            'buttons', 'stale_text'],
                     action=reverse('console:event_live_activity', args=[event.pk]))
    data.update(autosave=True, no_ai=True)
    return {'event_id': event.pk, 'name': f'Live Activity · {event.name}', 'form': data}
