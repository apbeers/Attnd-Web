"""More console components (stage A18): the rest of an organizer's work on the full-screen canvas and in the chat:
AI suggestions to review, app layout, website watching, the event's Details for the assistant, the flyer, the sharing
kit, lifecycle controls, whole-version restore and CSV exports. Each posts to the existing view (registered Ask Attnd
actions; the always-ask ones still ask).
"""

import json

from django.urls import reverse
from django.utils import timezone

from console.components import component, form_json
from console.components.catalog import _tz, editable_event, fmt


def _partial_event_form(event, rows, files=False):
    """EventForm with only `rows` visible; every other field goes along hidden with its current value, so saving this
    card changes nothing else (the flyer file input keeps the stored file when nothing is chosen)."""
    from console.forms.event_forms import EventForm
    with timezone.override(_tz(event)):
        form = EventForm(instance=event)
        data = form_json(form, rows, action=reverse('console:event_edit', args=[event.pk]),
                         submit=[{'label': 'Save', 'style': 'primary'}])
    shown = {r if isinstance(r, str) else None for r in rows}
    shown |= {n for r in rows if not isinstance(r, str) and r[0] == 'grid' for n in r[1:]}
    hidden = {}
    for name, f in data['fields'].items():
        if name in shown or f['widget'] == 'file':
            continue
        if f['widget'] in ('select', 'radio'):
            chosen = next((o['value'] for o in f.get('options') or [] if o['selected']), '')
            hidden[f['name']] = chosen
        elif f['widget'] == 'checkbox':
            if f.get('checked'):
                hidden[f['name']] = 'on'
        else:
            hidden[f['name']] = '' if f.get('value') is None else str(f['value'])
    data['hidden'] = hidden
    return data


# ---------------------------------------------------------------------------
# a. AI suggestions to review
# ---------------------------------------------------------------------------

def _review_args(action, post):
    if action != 'apply_ai_changes':
        return {}
    ids = sorted(int(k.split('_', 1)[1]) for k in post if k.startswith('accept_') and k.split('_', 1)[1].isdigit())
    return {'accept_change_ids': ids, 'notify_attendees': bool(post.get('notify_attendees'))}


def _expand_reviews(user, event_id=None, **_):
    from scrape.models import ChangeSet
    event = editable_event(user, event_id)
    return [{'event_id': event.pk, 'changeset_id': pk}
            for pk in event.changesets.filter(status=ChangeSet.PENDING).order_by('-created_at').values_list('pk', flat=True)[:10]]


@component('changeset_review', 'AI suggestions', "The AI's suggested changes from the event's websites / files waiting "
           'for review: each change old → new; tick the ones to keep and Apply, or Discard all (both ask to confirm). '
           'With only event_id: every pending review.', ids=('event_id', 'changeset_id'), expand=_expand_reviews,
           actions=(('apply_ai_changes', 'Apply'), ('discard_ai_changes', 'Discard all')), act_args=_review_args,
           page=lambda ids: reverse('console:changeset', args=[ids['changeset_id']]))
def changeset_review(user, event_id=None, changeset_id=None, event=None):
    from django.http import Http404

    from console.views_events import _changes_for_review
    from scrape.models import Change, ChangeSet
    event = editable_event(user, event_id, event)
    cs = ChangeSet.objects.filter(pk=changeset_id, event=event).first()
    if cs is None:
        raise Http404
    with timezone.override(_tz(event)):
        rows = _changes_for_review(cs)
    changes = []
    counts = {'create': 0, 'update': 0, 'delete': 0}
    for row in rows:
        ch = row['change']
        counts[ch.op] = counts.get(ch.op, 0) + 1
        changes.append({
            'id': ch.pk, 'op': ch.op, 'entity': ch.get_entity_display() if hasattr(ch, 'get_entity_display') else ch.entity,
            'title': ch.label if hasattr(ch, 'label') else (ch.data.get('name', {}) if isinstance(ch.data.get('name'), str) else ''),
            'pending': ch.status == Change.PENDING, 'conflict': bool(ch.conflict_fields),
            'fields': [{'name': f['name'], 'label': f['label'], 'old': str(f.get('old') or '')[:300], 'new': str(f.get('new') or '')[:300],
                        'conflict': f.get('conflict', False), 'image': f.get('is_image', False)} for f in row['fields']],
        })
    pending = cs.status == ChangeSet.PENDING
    summary = ', '.join(f'{n} {w}' for n, w in ((counts['create'], 'new'), (counts['update'], 'changed'),
                                                  (counts['delete'], 'removed')) if n)
    return {
        'id': cs.pk, 'event_id': event.pk, 'name': cs.summary or 'AI suggestions', 'summary': summary,
        'created': fmt(cs.created_at, 'M j, g:i A'), 'pending': pending, 'changes': changes,
        'stale': cs.base_version != event.content_version,
        'action': reverse('console:changeset', args=[cs.pk]),
        'actions': ([{'name': 'apply_ai_changes', 'style': 'primary'},
                     {'name': 'discard_ai_changes', 'style': 'danger', 'confirm_text': 'Discard every suggestion?'}]
                    if pending else []),
    }


# ---------------------------------------------------------------------------
# b. App layout · c. Website watching · d. Event details for the assistant · e. Flyer
# ---------------------------------------------------------------------------

@component('app_layout', 'App layout', "How the event looks in the apps: preset, tabs, default tab, list and schedule "
           'styles, map options, accent colour. Saves as you change it.',
           page=lambda ids: reverse('console:display', args=[ids['event_id']]))
def app_layout(user, event_id=None, event=None):
    from console.forms.event_forms import DisplayForm
    from events.display import clean_overrides, event_stats, resolve_display
    event = editable_event(user, event_id, event)
    form = DisplayForm(initial=DisplayForm.initial_from(clean_overrides(event.display_config)))
    data = form_json(form, ['preset', 'customize_tabs', 'tabs', ('grid', 'default_tab', 'secondary_pane'),
                            ('grid', 'locations_style', 'schedule_style'),
                            ('grid', 'map_clustering', 'map_fit_all'),
                            ('grid', 'show_location_images', 'enable_search'), 'group_by_category'],
                     action=reverse('console:display', args=[event.pk]), submit=[{'label': 'Save', 'style': 'primary'}])
    data['hidden'] = {'accent_color': event.accent_color or '', 'tab_order': form.initial.get('tab_order', '')}
    data['submit'] = []  # saves as you change it
    data['autosave'] = True
    resolved = resolve_display(event, event_stats(event))
    return {'event_id': event.pk, 'name': f'App layout · {event.name}', 'form': data,
            'resolved': {'preset': str(resolved.get('preset', '')), 'tabs': ', '.join(resolved.get('tabs') or [])},
            'accent': event.accent_color or '#431f86'}


@component('website_watching', 'Website watching', "The event's source websites, whether Attnd watches them for "
           'changes (review / small fixes automatically / everything automatically) and Check for updates now.',
           page=lambda ids: reverse('console:event', args=[ids['event_id']]))
def website_watching(user, event_id=None, event=None):
    from console.forms.event_forms import SourcesForm
    event = editable_event(user, event_id, event)
    data = form_json(SourcesForm(instance=event), ['source_urls_text', ('grid', 'refresh_schedule', 'update_mode'),
                                                   'source_text'],
                     action=reverse('console:event_sources', args=[event.pk]), submit=[{'label': 'Save', 'style': 'primary'}])
    checked = event.sources_checked_at
    return {'event_id': event.pk, 'name': f'Website watching · {event.name}', 'form': data,
            'watching': event.refresh_schedule == 'watch', 'checked': fmt(checked, 'M j, g:i A') if checked else '',
            'refresh_url': reverse('console:event_refresh', args=[event.pk]), 'has_sources': bool(event.source_urls)}


@component('event_context_form', 'Details for the assistant', "The event's Details for the assistant (what attendees' "
           'phones answer questions from, offline): about, FAQ, amenities, accessibility, policies, getting there, '
           'weather plan, links.', page=lambda ids: reverse('console:event_edit', args=[ids['event_id']]))
def event_context_form(user, event_id=None, event=None):
    from events import context as ctx
    event = editable_event(user, event_id, event)
    data = _partial_event_form(event, [f'ctx_{k}' for k in ctx.KEYS[ctx.EVENT]])
    return {'event_id': event.pk, 'name': f'Details for the assistant · {event.name}', 'form': data,
            'locked': 'context' in (event.locked_fields or [])}


@component('event_flyer', 'Flyer', "The event's flyer (an image or a PDF): upload, replace or remove it.",
           page=lambda ids: reverse('console:event_edit', args=[ids['event_id']]))
def event_flyer(user, event_id=None, event=None):
    event = editable_event(user, event_id, event)
    data = _partial_event_form(event, ['flyer'], files=True)
    url = event.flyer.url if event.flyer else ''
    return {'event_id': event.pk, 'name': f'Flyer · {event.name}', 'form': data, 'flyer_url': url,
            'is_pdf': url.lower().split('?')[0].endswith('.pdf')}


# ---------------------------------------------------------------------------
# f. Sharing kit · g. Lifecycle · h. Versions · i. Exports
# ---------------------------------------------------------------------------

@component('sharing_kit', 'Sharing kit', 'Everything to share the event: the join link and code (copy), the QR code '
           '(PNG / SVG download), a printable poster and table tent (A4 or Letter).',
           page=lambda ids: reverse('console:event_poster', args=[ids['event_id']]))
def sharing_kit(user, event_id=None, event=None):
    from attnd.links import join_url
    event = editable_event(user, event_id, event)
    poster = reverse('console:event_poster', args=[event.pk])
    return {'event_id': event.pk, 'name': f'Sharing kit · {event.name}', 'code': event.event_code,
            'join_url': join_url(event), 'qr_url': reverse('console:event_qr', args=[event.pk]),
            'qr_png': reverse('console:event_qr', args=[event.pk]) + '?download=1',
            'qr_svg': reverse('join:join_qr', args=[event.event_code]) + '?download=1',
            'prints': [{'label': 'Poster · Letter', 'url': f'{poster}?paper=letter'},
                       {'label': 'Poster · A4', 'url': f'{poster}?paper=a4'},
                       {'label': 'Table tent · Letter', 'url': f'{poster}?layout=tent&paper=letter'},
                       {'label': 'Table tent · A4', 'url': f'{poster}?layout=tent&paper=a4'}]}


@component('event_lifecycle', 'Status and visibility', "Publish, move back to draft, end (archive), list in nearby "
           'events or unlist, transfer ownership, delete; each asks to confirm (unlisting doesn\'t).',
           actions=(('publish_event', 'Publish'), ('unpublish_event', 'Move back to draft'), ('archive_event', 'End (archive)'),
                    ('list_event_nearby', 'List in nearby events'), ('unlist_event', 'Unlist'),
                    ('delete_event', 'Delete event')),
           page=lambda ids: reverse('console:event', args=[ids['event_id']]))
def event_lifecycle(user, event_id=None, event=None):
    from events.models import Event
    event = editable_event(user, event_id, event)
    role = event.role_for(user)
    manage = event.can_manage_team(user)
    actions = []
    if manage:
        if event.status != Event.STATUS_PUBLISHED:
            actions.append({'name': 'publish_event', 'style': 'primary', 'confirm_text': 'Publish? Attendees can join right away.'})
        if event.status != Event.STATUS_DRAFT:
            actions.append({'name': 'unpublish_event', 'style': 'outline', 'confirm_text': 'Move back to draft? Attendees can no longer open it.'})
        if event.status != Event.STATUS_ARCHIVED:
            actions.append({'name': 'archive_event', 'style': 'outline', 'confirm_text': 'End the event? Attendees see it as ended.'})
        actions.append({'name': 'unlist_event', 'style': 'outline'} if event.public else
                       {'name': 'list_event_nearby', 'style': 'outline', 'confirm_text': 'List it in nearby events for everyone?'})
    if role == 'owner':
        actions.append({'name': 'delete_event', 'style': 'danger', 'fields': {'confirm_code': event.event_code},
                        'confirm_text': 'Delete this event permanently? This can’t be undone.'})
    return {'event_id': event.pk, 'name': f'Status · {event.name}', 'status': event.get_status_display(),
            'public': event.public, 'manage': manage, 'owner': role == 'owner',
            'team_url': reverse('console:team', args=[event.pk]), 'actions': actions}


@component('event_versions', 'Versions', "Earlier versions of an event's content (details, locations, schedule) to "
           'restore as a whole (asks to confirm), and Save a snapshot.',
           actions=(('restore_version', 'Restore'), ('save_snapshot', 'Save a snapshot')),
           page=lambda ids: reverse('console:history', args=[ids['event_id']]))
def event_versions(user, event_id=None, event=None):
    event = editable_event(user, event_id, event)
    rows = []
    actions = [{'name': 'save_snapshot', 'style': 'outline'}]
    with timezone.override(_tz(event)):
        for rev in event.revisions.select_related('created_by')[:15]:
            row = {'id': rev.pk, 'when': fmt(rev.created_at, 'M j, g:i A'), 'summary': rev.summary or 'Version',
                   'who': rev.created_by.email if rev.created_by_id else 'Attnd',
                   'actions': [{'name': 'restore_version', 'style': 'outline', 'fields': {'revision': rev.pk},
                                'confirm_text': 'Restore this version? The current content is replaced (you can undo it from here).'}]}
            rows.append(row)
    from console.components import get, resolve_actions
    for row in rows:
        row['actions'] = [dict(a, **{k: v for k, v in r.items() if k != 'name'}) for a, r in
                          zip(row['actions'], resolve_actions(get('event_versions'), {'event_id': event.pk}, row['actions']))]
        actions += row['actions']
    return {'event_id': event.pk, 'name': f'Versions · {event.name}', 'rows': rows, 'actions': actions}


EXPORTS = (('schedule', 'Schedule'), ('locations', 'Locations'), ('notifications', 'Notifications'),
           ('metrics', 'Metrics'))


@component('event_exports', 'Export', 'Download the schedule, locations, notifications or metrics as CSV '
           '(opens in Excel, Numbers, Google Sheets).',
           page=lambda ids: reverse('console:event', args=[ids['event_id']]))
def event_exports(user, event_id=None, event=None):
    event = editable_event(user, event_id, event)
    return {'event_id': event.pk, 'name': f'Export · {event.name}',
            'files': [{'label': label, 'url': reverse('console:export', args=[event.pk, kind])} for kind, label in EXPORTS]}


__all__ = ['json']
