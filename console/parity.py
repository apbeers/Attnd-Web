"""Page ↔ chat parity (batch 7 Track B): every console feature exists both on the classic pages and in the Ask Attnd
chat, through the shared components (console/components) and registered actions (agent/registry.py), never copied
code. `table(user, event)` builds the table from the code itself: for every console URL, the page's read action, its
write tools, the components a page or the canvas draws for it and how the chat reaches each write (a component's
button or form). `docs/parity.md` is that table; console/tests_parity.py fails when a write has no way in from the
chat (and isn't in TOOL_ONLY with its reason), when a page is neither drawn by a component nor in BESPOKE, or when the
doc is out of date (refresh: UPDATE_PARITY=1 uv run python manage.py test console.tests_parity)."""

import re

from django.urls import NoReverseMatch, Resolver404, resolve

# Writes the chat reaches only by asking (the model calls the registered tool); no card has a button for them, on
# purpose. Each needs its reason.
TOOL_ONLY = {
    'design_website': 'Website changes are asked for in the chat (batch 5: no design box on the card); Ask Attnd '
                      'calls this tool.',
    'retry_studio_run': "The live task card's Retry for a failed Studio run (agent.js, from the task feed).",
    'retry_background_task': "The live task card's Retry for a failed import (agent.js, from the task feed).",
    'set_chat_default': "The top bar's chat switch on every page (base.html), not a card.",
    'undo_last_change': 'The Undo in every toast and next to every chat step (base.html, agent.js), not a card.',
    'undo_change': 'The Undo in every toast and next to every chat step (base.html, agent.js), not a card.',
    'import_into_event': 'Paste or drop text or a file onto the chat or the canvas (agent.js); no card for it.',
    'reorder_locations': 'Drag to reorder on the Locations page and the canvas tab (console.js, agent.js).',
    'edit_website_text': "Edit text inside the full screen preview (the preview's own editor), not a card button.",
}
# Buttons a component draws only in some states (the sample event doesn't show them): tool → where and when.
SHOWN_WHEN = {
    'billing_portal': 'event_billing: once Stripe is on and the event was paid by card',
    'request_limit_removal': 'event_lifecycle: while the free phone limit applies',
    'fill_in_details': 'event_readiness: when places or items have no details',
    'find_music_links': 'event_readiness: when performers have no Apple Music links',
    'translate_website': 'website_overview: once the website has a version',
}

# Pages that stay their own screen (not drawn by components), with the reason (CLAUDE.md → Components: bespoke
# screens are not shown in the chat). Ask Attnd still reads them with read_page and acts through their write tools.
_STUDIO = "Studio's code editor and site settings (a bespoke screen; Ask Attnd reads it and calls its tools)"
BESPOKE = {
    'studio:code': _STUDIO, 'studio:undo': _STUDIO, 'studio:preview_link': _STUDIO, 'studio:settings': _STUDIO,
    'studio:assets': _STUDIO, 'studio:asset': _STUDIO, 'studio:flyer': _STUDIO,
    'studio:form_csv': 'A CSV download of a form\'s entries (website_forms links to it).',
    'console:tasks': 'The background task feed: JSON for the tray and the chat\'s live task cards.',
    'console:job': "An AI import's progress page; the chat shows the same steps as a live task card.",
    'console:event_invoices_csv': 'A CSV download of the invoices (event_billing links to it).',
    'console:event_invoice_pdf': 'An invoice PDF download (event_billing links to it).',
    'console:admin_usage': 'Usage across every event (platform admins): a bespoke staff table.',
}
# Whole areas that are bespoke staff / Studio screens: their pages and the buttons on them (url name prefix → reason).
BESPOKE_AREAS = {
    'diagnostics:': 'Diagnostics: a bespoke staff screen (CLAUDE.md → Components).',
    'platform:': 'The Platform admin: bespoke staff screens (Health is the platform_health component).',
    'prospects:': 'Prospects: bespoke staff screens with a bulk-select table (rows and detail are components).',
    'studio:reports': 'Studio abuse reports: a bespoke staff screen.',
}


def bespoke(url_name):
    if url_name in BESPOKE:
        return BESPOKE[url_name]
    return next((reason for prefix, reason in BESPOKE_AREAS.items() if url_name.startswith(prefix)), '')


def _walk(value, found):
    """Collect (kind, value) pairs from a component's JSON: offered actions (resolved: name + url) and forms."""
    if isinstance(value, dict):
        if 'name' in value and 'url' in value and 'confirm' in value:
            found.add(('action', value['name']))
        if isinstance(value.get('form'), dict) and value['form'].get('action'):
            found.add(('form', value['form']['action']))
        if 'action' in value and 'fields' in value and isinstance(value.get('fields'), (dict, list)) \
                and isinstance(value.get('action'), str) and value['action'].startswith('/'):
            found.add(('form', value['action']))
        for v in value.values():
            _walk(v, found)
    elif isinstance(value, str) and value.startswith('/console/') and len(value) < 300:
        found.add(('form', value))  # a URL the card posts to or opens (a form's action, a check's fix button)
    elif isinstance(value, (list, tuple)):
        for v in value:
            _walk(v, found)


def _url_name(path):
    try:
        match = resolve(path.split('?')[0].split('#')[0])
    except Resolver404:
        return ''
    return f'{match.namespace}:{match.url_name}' if match.namespace else match.url_name


def sample_ids(event):
    """One of each kind of row of the event (the first location, schedule item, a scheduled and a sent notification,
    the latest AI review), so components with those ids are drawn too."""
    from scrape.models import ChangeSet
    out = {'event_id': [event.pk]}
    for key, qs in (('location_id', event.location_set.order_by('pk')),
                    ('item_id', event.scheduleditem_set.order_by('pk')),
                    ('changeset_id', ChangeSet.objects.filter(event=event).order_by('-pk'))):
        first = qs.values_list('pk', flat=True).first()
        if first:
            out[key] = [first]
    notes = list(event.notification_set.order_by('sent', 'pk').values_list('pk', 'sent'))
    out['notification_id'] = ([pk for pk, sent in notes if not sent][:1] + [pk for pk, sent in notes if sent][:1])
    try:
        from prospects.models import Prospect, ProspectRun
        out['prospect_id'] = list(Prospect.objects.order_by('pk').values_list('pk', flat=True)[:1])
        out['prospect_run_id'] = list(ProspectRun.objects.order_by('pk').values_list('pk', flat=True)[:1])
    except Exception:
        pass
    return {k: v for k, v in out.items() if v}


def _samples(comp, samples):
    """The id sets to draw a component with: its parent only (lists expand), then with each optional / required id."""
    base = {'event_id': samples['event_id'][0]} if 'event_id' in comp.all_ids else {}
    out = []
    if all(k in base for k in comp.ids) or comp.expand:
        out.append([base])
    for key in comp.all_ids:
        if key == 'event_id' or key not in samples:
            continue
        sets = [{**base, key: v} for v in samples[key]]
        if all(all(k in ids for k in comp.ids) for ids in sets):
            out.append(sets)
    return out


def chat_coverage(user, event):
    """{write tool: [how the chat reaches it]} from every component's declared actions and from what the
    components actually draw for `event` (buttons and forms, resolved to their write tools)."""
    from agent import registry
    from console import components as C
    writes = registry.registry()['writes']
    by_url = {}
    for name, action in writes.items():
        by_url.setdefault(action.url_name, []).append(name)
    out = {}
    pages = {}
    samples = sample_ids(event)
    for cname, comp in sorted(C.for_user(user).items()):
        for tool, _label in comp.actions:
            out.setdefault(tool, set()).add(f'{cname} (button)')
        found_data = []
        for items in _samples(comp, samples):
            try:
                found_data += C.instances(user, cname, items, limit=3)
            except Exception:  # a component that needs other ids or data: its declared actions still count
                continue
        found = set()
        for ids, data in found_data:
            _walk(data, found)
            if comp.page:
                try:
                    pages.setdefault(_url_name(comp.page(ids)), set()).add(cname)
                except (KeyError, NoReverseMatch):
                    pass
        for kind, value in found:
            if kind == 'action':
                out.setdefault(value, set()).add(f'{cname} (button)')
            else:
                for tool in by_url.get(_url_name(value), []):
                    out.setdefault(tool, set()).add(f'{cname} (form)')
    return {k: sorted(v) for k, v in out.items()}, {k: sorted(v) for k, v in pages.items() if k}


KWARG_SAMPLES = {'event_id': 'event_id', 'location_id': 'location_id', 'item_id': 'item_id',
                 'changeset_id': 'changeset_id', 'notification_id': 'notification_id'}
COMPONENT_CLASS = re.compile(r'\bcomponent--([a-z_]+)\b')


def drawn(client, user, event, route_kwargs, url_name):
    """The components a page actually draws (their root class in its HTML), fetched as `user` through `client`."""
    from django.urls import reverse
    samples = sample_ids(event)
    kwargs = {}
    for key, _conv in route_kwargs:
        sample = KWARG_SAMPLES.get(key)
        if sample not in samples:
            return None
        kwargs[key] = samples[sample][0]
    try:
        url = reverse(url_name, kwargs=kwargs)
    except NoReverseMatch:
        return None
    response = client.get(url + ('?page=1' if '?' not in url else ''), secure=True)
    if response.status_code != 200 or 'text/html' not in response.get('Content-Type', ''):
        return None
    return sorted(set(COMPONENT_CLASS.findall(response.content.decode())))


def table(user, event, client=None):
    """[{url, route, read, writes: [{tool, chat}], components, bespoke}] for every console URL, in URL order.
    With a signed-in `client` the pages are fetched and the components they draw read from their HTML."""
    from agent import registry
    reg = registry.registry()
    coverage, pages = chat_coverage(user, event)
    if client is not None:
        for route, name, _pattern in registry.console_patterns():
            if name in reg['reads'] and name not in registry.EXCLUDED:
                found = drawn(client, user, event, reg['reads'][name].kwargs, name)
                if found:
                    pages[name] = sorted(set(pages.get(name, [])) | set(found))
    writes_by_url = {}
    for name, action in reg['writes'].items():
        writes_by_url.setdefault(action.url_name, []).append(name)
    rows = []
    for route, name, _pattern in registry.console_patterns():
        if not name or name in registry.EXCLUDED:
            continue
        read = reg['reads'].get(name)
        rows.append({
            'url': name, 'route': '/' + route, 'read': read.name if read else '',
            'writes': [{'tool': t, 'chat': coverage.get(t) or ([f'tool only: {TOOL_ONLY[t]}'] if t in TOOL_ONLY else
                                                                [f'shown by {SHOWN_WHEN[t]}'] if t in SHOWN_WHEN else
                                                                ['its page\'s own button (bespoke)'] if bespoke(name) else [])}
                       for t in sorted(writes_by_url.get(name, []))],
            'components': pages.get(name, []), 'bespoke': bespoke(name),
        })
    return rows


def problems(rows):
    out = []
    for r in rows:
        for w in r['writes']:
            if not w['chat'] and not r['bespoke']:
                out.append(f"{r['url']}: the write {w['tool']} has no button or form in any component (add one, or "
                           'TOOL_ONLY with the reason)')
        if r['read'] and not r['components'] and not r['bespoke']:
            out.append(f"{r['url']}: the page is drawn by no component (draw it with components, or BESPOKE with "
                       'the reason)')
    return out


def markdown(rows):
    lines = ['# Page ↔ chat parity', '',
             'Generated by `console/parity.py` (refresh: `UPDATE_PARITY=1 uv run python manage.py test '
             'console.tests_parity`). Every console URL: the page Ask Attnd reads, the components that draw it, and '
             'for each write tool how the chat reaches it (a component\'s button or form; "tool only" = Ask Attnd '
             'calls it when asked, with the reason). URLs in `agent/registry.py` EXCLUDED are left out.', '',
             '| URL | Page (read) | Drawn by | Writes → in the chat via |', '|---|---|---|---|']
    for r in rows:
        writes = '<br>'.join(f"`{w['tool']}` → {', '.join(w['chat']) or '**missing**'}" for w in r['writes']) or '—'
        drawn = ', '.join(f'`{c}`' for c in r['components']) or (f"bespoke: {r['bespoke']}" if r['bespoke'] else '—')
        lines.append(f"| `{r['url']}`<br><span>{r['route']}</span> | {('`' + r['read'] + '`') if r['read'] else '—'} "
                     f"| {drawn} | {writes} |")
    return '\n'.join(lines) + '\n'
