"""Attnd Studio inside Ask Attnd (batch 5, owner Nov 21, 2026): the event's website as components, for the full-screen
chat's Website tab and the Website page (studio:editor). Ask Attnd is the one chat: changes go through the registered
action design_website (Studio's engine in the background, a live task card), publishing through publish_website /
unpublish_website / restore_website_version (always ask). Audience 'studio': staff, superusers and
STUDIO_BETA_EMAILS (studio.access), like every Studio URL.
"""

from django import forms
from django.urls import reverse
from django.utils import timezone

from console.components import component, form_json
from console.components.catalog import _tz, editable_event, fmt


def _event(user, event_id, event):
    from django.http import Http404

    from studio.access import has_studio_access
    if not has_studio_access(user):
        raise Http404
    return editable_event(user, event_id, event)


def _site(event):
    from studio import publishing
    return publishing.ensure_site(event) if event.plan == event.PLAN_STUDIO else None


class StartForm(forms.Form):
    """No fields: the empty state's buttons are background forms (the task appears as a live card in the chat)."""


def _start_form(action, label, style, busy):
    data = form_json(StartForm(), [], action=action, submit=[{'label': label, 'style': style}])
    data['background'] = busy
    data['no_ai'] = True
    return data


@component('website_overview', 'Website', "The event's Attnd Studio website: the live preview of the draft, which "
           'version is live, its address once published, Publish / Unpublish (always ask). Without a built version: '
           '"No website yet" with Build from your current website / Start from scratch. Changes are asked for in the '
           'chat (design_website). Without Studio on the event: Use Studio for this event.',
           actions=(('enable_studio', 'Use Studio for this event'), ('publish_website', 'Publish the draft'),
                    ('unpublish_website', 'Take the site offline')),
           audience='studio', page=lambda ids: reverse('studio:editor', args=[ids['event_id']]))
def website_overview(user, event_id=None, event=None):
    from studio import hosting
    from studio.access import can_publish, can_switch_plan
    from studio.models import StudioRun
    from studio.views import run_task
    event = _event(user, event_id, event)
    site = _site(event)
    base = {'event_id': event.pk, 'name': f'Website · {event.name}', 'enabled': site is not None,
            'page_url': reverse('studio:editor', args=[event.pk])}
    if site is None:
        actions = [{'name': 'enable_studio', 'style': 'primary'}] if can_switch_plan(event, user) else []
        return {**base, 'actions': actions}
    draft, live = site.draft, site.published
    actions = []
    if can_publish(event, user):
        if draft and 'index.html' in (draft.files or {}) and not site.suspended and (not live or live.pk != draft.pk):
            actions.append({'name': 'publish_website', 'style': 'primary'})
        if live:
            actions.append({'name': 'unpublish_website', 'style': 'outline'})
    active = site.runs.filter(status__in=[StudioRun.QUEUED, StudioRun.RUNNING]).first()
    built = bool(draft and 'index.html' in (draft.files or {}))
    starts = []
    if not built and not active:
        source = website_source(event)
        if source:
            starts.append(_start_form(reverse('studio:import', args=[event.pk]), 'Build from your current website',
                                      'primary', 'Building from your website…'))
            starts[-1]['hidden'] = {'url': source}
        starts.append(_start_form(reverse('studio:start', args=[event.pk]), 'Start from scratch',
                                  'outline' if source else 'primary', 'Starting your website…'))
    live_shown = bool(live and not site.suspended)
    return {**base, 'actions': actions if built else [a for a in actions if a['name'] != 'publish_website'],
            'built': built, 'source': website_source(event) if not built else '',
            # A preview only of a version with a home page (an empty draft would answer "Not found").
            'preview': hosting.preview_url(site) if hosting.sites_origin() and built else '',
            'draft': draft.number if draft and built else None, 'live': live.number if live else None,
            # The address only once published (never the raw sites host of a draft).
            'public_url': site.public_url() if live_shown else '',
            'suspended': site.suspended, 'pages': draft.pages if built else [],
            'working': run_task(active) if active else None, 'starts': starts}


def website_source(event):
    """The event's current website to build from (https only), or ''."""
    from events.context import https_url
    for url in (event.source_urls or []):
        if https_url(url) or (isinstance(url, str) and url.startswith('http://')):
            return url
    return ''


@component('website_versions', 'Website versions', "Every version of the event's website (newest first, which one is "
           'live and the draft), each with Preview, Restore as the draft and Publish (both always ask).',
           actions=(('restore_website_version', 'Restore as the draft'), ('publish_website_version', 'Publish this one')),
           audience='studio', page=lambda ids: reverse('studio:editor', args=[ids['event_id']]))
def website_versions(user, event_id=None, event=None):
    from studio.access import can_publish
    event = _event(user, event_id, event)
    site = _site(event)
    rows = []
    if site is not None:
        publish = can_publish(event, user)
        with timezone.override(_tz(event)):
            for v in site.versions.select_related('created_by').order_by('-number')[:30]:
                actions = []
                if v.pk != site.draft_id:
                    actions.append({'name': 'restore_website_version', 'style': 'outline', 'params': {'version': str(v.pk)}})
                if publish and v.pk != site.published_id and 'index.html' in (v.files or {}) and not site.suspended:
                    actions.append({'name': 'publish_website_version', 'style': 'outline', 'params': {'version': str(v.pk)}})
                rows.append({'id': v.pk, 'number': v.number, 'summary': v.summary or 'Version',
                             'source': v.get_source_display(), 'when': fmt(v.created_at, 'M j, g:i A'),
                             'live': v.pk == site.published_id, 'draft': v.pk == site.draft_id,
                             'preview': reverse('studio:version_preview', args=[event.pk, v.pk]), 'actions': actions})
    from console.components import get, resolve_actions
    comp = get('website_versions')
    for row in rows:
        row['actions'] = resolve_actions(comp, {'event_id': event.pk}, row['actions'])
    return {'event_id': event.pk, 'name': f'Website versions · {event.name}', 'enabled': site is not None,
            'rows': rows, 'actions': [a for r in rows for a in r['actions']]}


@component('website_assets', 'Website files', "The website's asset library (images, PDFs, fonts the designer uses): "
           'name, kind, size, with Delete (always asks).', actions=(('delete_studio_asset', 'Delete'),),
           audience='studio', page=lambda ids: reverse('studio:editor', args=[ids['event_id']]))
def website_assets(user, event_id=None, event=None):
    from studio import hosting
    from studio.models import StudioAsset
    event = _event(user, event_id, event)
    site = _site(event)
    rows = []
    if site is not None:
        origin = hosting.sites_origin()
        for a in StudioAsset.objects.filter(event=event).order_by('-created_at')[:60]:
            rows.append({'id': str(a.pk), 'name': a.name, 'kind': a.get_kind_display(), 'size': a.size,
                         'thumb': f'{origin}/{site.slug}/{a.site_path}' if origin and a.kind == StudioAsset.KIND_IMAGE else '',
                         'actions': [{'name': 'delete_studio_asset', 'style': 'ghost', 'params': {'asset': str(a.pk)}}]})
    from console.components import get, resolve_actions
    comp = get('website_assets')
    for row in rows:
        row['actions'] = resolve_actions(comp, {'event_id': event.pk}, row['actions'])
    return {'event_id': event.pk, 'name': f'Website files · {event.name}', 'enabled': site is not None, 'rows': rows,
            'actions': [a for r in rows for a in r['actions']]}


@component('website_forms', 'Website form entries', "What visitors sent through the website's forms (sign-ups, "
           'vendor applications…): the latest entries per form, CSV download, Delete an entry (always asks).',
           actions=(('delete_studio_form_entry', 'Delete'),),
           audience='studio', page=lambda ids: reverse('studio:editor', args=[ids['event_id']]) + '#website-forms')
def website_forms(user, event_id=None, event=None):
    from studio.views import _forms_summary
    event = _event(user, event_id, event)
    site = _site(event)
    forms_ = []
    if site is not None:
        with timezone.override(_tz(event)):
            for f in _forms_summary(site):
                recent = [{'id': r['id'], 'when': fmt(r['at'], 'M j, g:i A'), 'values': r['values'],
                           'actions': [{'name': 'delete_studio_form_entry', 'style': 'ghost', 'params': {'entry': str(r['id'])}}]}
                          for r in f['recent'][:10]]
                forms_.append({'name': f['form'], 'count': f['count'], 'fields': f['fields'], 'recent': recent,
                               'csv': reverse('studio:form_csv', args=[event.pk, f['form']])})
    from console.components import get, resolve_actions
    comp = get('website_forms')
    for f in forms_:
        for r in f['recent']:
            r['actions'] = resolve_actions(comp, {'event_id': event.pk}, r['actions'])
    return {'event_id': event.pk, 'name': f'Website forms · {event.name}', 'enabled': site is not None, 'forms': forms_,
            'actions': [a for f in forms_ for r in f['recent'] for a in r['actions']]}
