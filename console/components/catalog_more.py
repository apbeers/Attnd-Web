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


def _small_event_form(event, cls, rows, part):
    """A cut-down event form (only its own fields; the view saves only those, `part`)."""
    with timezone.override(_tz(event)):
        data = form_json(cls(instance=event), rows, action=reverse('console:event_edit', args=[event.pk]),
                         submit=[{'label': 'Save', 'style': 'primary'}], locked=list(event.locked_fields or []))
    data['hidden'] = {'part': part}
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
    return [{'event_id': event.pk, 'changeset_id': pk, 'event': event}
            for pk in event.changesets.filter(status=ChangeSet.PENDING).order_by('-created_at').values_list('pk', flat=True)]


@component('changeset_review', 'AI suggestions', "The AI's suggested changes from the event's websites / files waiting "
           'for review: each change old → new; tick the ones to keep and Apply, or Discard all (both ask to confirm). '
           'With only event_id: every pending review.', ids=('event_id', 'changeset_id'), expand=_expand_reviews,
           actions=(('apply_ai_changes', 'Apply'), ('discard_ai_changes', 'Discard all')), act_args=_review_args,
           page=lambda ids: reverse('console:changeset', args=[ids['changeset_id']]))
def changeset_review(user, event_id=None, changeset_id=None, event=None, changeset=None):
    from django.http import Http404

    from console.views_events import _changes_for_review
    from scrape.models import Change, ChangeSet
    event = editable_event(user, event_id, event)
    cs = changeset if changeset is not None else ChangeSet.objects.filter(pk=changeset_id, event=event).first()
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
            'id': ch.pk, 'op': ch.op, 'entity': ch.get_entity_display(), 'title': ch.label,
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

EMPTY_NOTES = {
    'schedule': 'Empty: no visible schedule items', 'locations': 'Empty: no visible locations',
    'map': 'Empty: no location has a pin', 'flyer': 'Empty: no flyer uploaded',
    'notifications': 'Empty: no notification sent yet',
}


@component('app_layout', 'App layout', "How the event looks in the apps: preset, tabs, default tab, list and schedule "
           'styles, map options, accent colour. Saves as you change it.',
           page=lambda ids: reverse('console:display', args=[ids['event_id']]))
def app_layout(user, event_id=None, event=None, form=None):
    from console.forms.event_forms import DisplayForm
    from events.display import LABELS, TABS, clean_overrides, event_stats, resolve_display
    from events.layout import CATALOGUE_VERSION
    event = editable_event(user, event_id, event)
    overrides = clean_overrides(event.display_config)
    form = form or DisplayForm(initial=DisplayForm.initial_from(overrides))
    initial = DisplayForm.initial_from(overrides)
    order = [t for t in (initial.get('tab_order') or '').split(',') if t in TABS]
    order += [t for t in TABS if t not in order]
    chosen = set(initial.get('tabs') or [])
    stats = event_stats(event)
    from events.display import empty_tabs
    empty = empty_tabs(stats)  # the apps leave these out automatically; chosen by hand, they'd show nothing
    resolved = resolve_display(event, stats, sections=CATALOGUE_VERSION)  # the newest apps' tabs (Home included)
    from events.display import MAX_TABS, TAB_LIMIT_NOTE, fit_tabs
    _, cut = fit_tabs([t for t in order if t in chosen]) if 'tabs' in overrides else ([], [])
    data = form_json(form, [('choices', 'preset'),
                            ('block', 'layout_reason', {'text': f"Currently using {str(resolved.get('resolved_preset', '')).capitalize()}: {resolved.get('reason', '')}."}),
                            'customize_tabs',
                            ('block', 'tab_list', {'tabs': [{'value': t, 'label': LABELS['tabs'].get(t, t), 'checked': t in chosen,
                                                             'empty': EMPTY_NOTES.get(t) if t in empty else ''}
                                                            for t in order],
                                                   'max': MAX_TABS, 'chosen': len(chosen), 'note': TAB_LIMIT_NOTE,
                                                   'cut': ', '.join(LABELS['tabs'].get(t, t) for t in cut)}),
                            ('grid', 'default_tab', 'secondary_pane'), ('grid', 'locations_style', 'schedule_style'),
                            ('grid', 'map_clustering', 'map_fit_all'), ('grid', 'show_location_images', 'enable_search'),
                            'group_by_category',
                            ('block', 'accent', {'value': event.accent_color or '#431f86', 'default': not event.accent_color})],
                     action=reverse('console:display', args=[event.pk]), submit=[])
    data['hidden'] = {'tab_order': ','.join(order)}
    data['autosave'] = True
    return {'event_id': event.pk, 'name': f'App layout · {event.name}', 'form': data,
            'counts': {'locations': stats.get('location_count', 0), 'items': stats.get('item_count', 0),
                       'spread': stats.get('spread_km')},
            'resolved': {'preset': str(resolved.get('resolved_preset', '')), 'tabs': ', '.join(resolved.get('tabs') or [])}}


@component('event_theme', 'Colour theme', "The event's own colours (API-023): primary (buttons, links), secondary "
           '(soft tints) and tertiary (highlights) for light and dark mode as exact hex codes with contrast notes, a '
           'preview, Generate with AI, Reset to Attnd\'s colours, and switches for the website and the apps.',
           actions=(('reset_event_theme', 'Reset to Attnd’s colours'),), ai_subject='colour theme',
           ai_prompt='Pick colours for this event from its website, logo and photos.',
           page=lambda ids: reverse('console:event_theme', args=[ids['event_id']]))
def event_theme(user, event_id=None, event=None, form=None, full=False):
    """The compact card (swatches, switches, Generate with AI, Edit); `full` = the manual editor itself
    (event_theme_editor: the theme page, the canvas overlay; on pages the compact card opens it in a <dialog>)."""
    from console.forms.event_forms import EventThemeForm, EventThemeSwitchesForm, ThemeGenerateForm
    from events import theme as T
    event = editable_event(user, event_id, event)
    theme = T.normalize(event.theme)
    form = form or EventThemeForm(theme=event.theme)
    shown = theme or T.default_theme()
    preview = []
    for mode in T.MODES:
        t = T.tokens(shown[mode], mode)
        page = T.PAGE[mode]
        preview.append({'mode': mode, 'label': 'Light' if mode == 'light' else 'Dark',
                        'style': ';'.join(f'--pv-{k}:{v}' for k, v in (
                            ('brand', t['--color-brand']), ('on', t['--color-on-brand']),
                            ('text', t['--color-brand-text']), ('soft', t['--color-brand-soft']),
                            ('line', t['--color-brand-line']), ('mid', t['--color-brand-mid']),
                            ('bg', page['surface']), ('ink', page['text'])))})
    colours = form_json(form, [('block', 'theme_preview', {'modes': preview}),
                               ('grid', 'light_primary', 'dark_primary'), ('grid', 'light_secondary', 'dark_secondary'),
                               ('grid', 'light_tertiary', 'dark_tertiary')],
                        action=reverse('console:event_theme', args=[event.pk]),
                        submit=[{'label': 'Save colours', 'style': 'primary'}])
    switches = form_json(EventThemeSwitchesForm(initial={'theme_web': event.theme_web, 'theme_apps': event.theme_apps}),
                         ['theme_web', 'theme_apps'], action=reverse('console:event_theme_switches', args=[event.pk]))
    switches.update(autosave=True, no_ai=True)
    generate = form_json(ThemeGenerateForm(), ['instruction'],
                         action=reverse('console:event_theme_generate', args=[event.pk]),
                         submit=[{'label': 'Generate with AI', 'style': 'outline', 'icon': 'stars'}])
    generate['background'] = 'Picking colours…'
    # The compact card's one-button version (no instruction field; the full editor has it).
    generate_quick = form_json(ThemeGenerateForm(), [], action=reverse('console:event_theme_generate', args=[event.pk]),
                               submit=[{'label': 'Generate with AI', 'style': 'outline', 'icon': 'stars'}])
    generate_quick.update(background='Picking colours…', no_ai=True)
    source = theme.get('source', '') if theme else ''
    swatches = [{'mode': mode, 'role': role, 'hex': shown[mode][role],
                 'label': f'{"Light" if mode == "light" else "Dark"} {role}: {shown[mode][role]}'}
                for mode in T.MODES for role in T.ROLES]
    return {
        'swatches': swatches, 'generate_quick': generate_quick, 'full': full,
        'editor_url': reverse('console:event_theme', args=[event.pk]),
        'event_id': event.pk, 'name': f'Colours · {event.name}', 'form': colours, 'switches': switches,
        'generate': generate, 'custom': bool(theme), 'locked': 'theme' in (event.locked_fields or []),
        'source': {'ai': 'Picked by the AI', 'site': 'From your website’s colours', 'manual': 'Chosen by your team'
                   }.get(source, 'Attnd’s colours'),
        'reason': theme.get('reason', '') if theme else '',
        'web': event.theme_web, 'apps': event.theme_apps,
        'actions': [{'name': 'reset_event_theme', 'style': 'ghost'}] if theme else [],
    }


@component('event_theme_editor', 'Colour theme editor', "The full manual editor of an event's colours: the six hex "
           'codes with pickers and contrast notes, a preview per mode, Save, Generate with AI with an instruction, Reset.',
           actions=(('reset_event_theme', 'Reset to Attnd’s colours'),), ai_subject='colour theme',
           ai_prompt='Pick colours for this event from its website, logo and photos.', template='components/event_theme.html',
           page=lambda ids: reverse('console:event_theme', args=[ids['event_id']]))
def event_theme_editor(user, event_id=None, event=None, form=None):
    return event_theme(user, event_id=event_id, event=event, form=form, full=True)


@component('website_watching', 'Website watching', "The event's source websites, whether Attnd watches them for "
           'changes (review / small fixes automatically / everything automatically) and Check for updates now.',
           page=lambda ids: reverse('console:event', args=[ids['event_id']]))
def website_watching(user, event_id=None, event=None):
    from console.forms.event_forms import RefreshForm, SourcesForm
    from django.contrib.humanize.templatetags.humanize import naturaltime
    from django.utils.timesince import timesince
    event = editable_event(user, event_id, event)
    manage = event.can_manage_team(user)
    data = form_json(SourcesForm(instance=event), ['source_urls_text', ('grid', 'refresh_schedule', 'update_mode'),
                                                   'source_text'],
                     action=reverse('console:event_sources', args=[event.pk]),
                     submit=[{'label': 'Save', 'style': 'secondary'}] if manage else [])
    refresh = form_json(RefreshForm(), ['text', 'upload', 'instructions'],
                        action=reverse('console:event_refresh', args=[event.pk]),
                        submit=[{'label': 'Check for updates now', 'style': 'outline', 'icon': 'refresh'}])
    refresh['background'] = 'Checking for updates…'
    pages = []
    for p in event.source_pages.all()[:40]:
        if p.has_pending_change:
            state = 'changed, AI update pending'
        elif p.changed_at:
            state = f'last changed {naturaltime(p.changed_at)}'
        elif p.checked_at:
            state = 'no changes yet'
        else:
            state = 'not checked yet'
        pages.append({'url': p.url, 'state': state, 'error': p.last_status if p.failures else ''})
    checked = event.sources_checked_at
    return {'event_id': event.pk, 'name': f'Website watching · {event.name}', 'form': data, 'refresh': refresh,
            'manage': manage, 'watching': event.refresh_schedule == 'watch', 'pages': pages,
            'checked': fmt(checked, 'M j, g:i A') if checked else '',
            'last_ai': timesince(event.last_refreshed_at) if event.last_refreshed_at else '',
            'has_sources': bool(event.source_urls)}


@component('event_context_form', 'Details for the assistant', "The event's Details for the assistant (what attendees' "
           'phones answer questions from, offline): about, FAQ, amenities, accessibility, policies, getting there, '
           'weather plan, links.', page=lambda ids: reverse('console:event_edit', args=[ids['event_id']]),
           chat_only='The canvas cut-down of event_form (the Details page shows the whole form).')
def event_context_form(user, event_id=None, event=None):
    from events import context as ctx
    event = editable_event(user, event_id, event)
    from console.forms.event_forms import EventContextForm
    data = _small_event_form(event, EventContextForm, [f'ctx_{k}' for k in ctx.KEYS[ctx.EVENT]], 'context')
    return {'event_id': event.pk, 'name': f'Details for the assistant · {event.name}', 'form': data,
            'locked': 'context' in (event.locked_fields or [])}


@component('event_flyer', 'Flyer', "The event's flyer (an image or a PDF): upload, replace or remove it.",
           page=lambda ids: reverse('console:event_edit', args=[ids['event_id']]),
           chat_only='The canvas cut-down of event_form (the Details page shows the whole form).')
def event_flyer(user, event_id=None, event=None):
    event = editable_event(user, event_id, event)
    from console.forms.event_forms import EventFlyerForm
    data = _small_event_form(event, EventFlyerForm, ['flyer'], 'flyer')
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
                    ('delete_event', 'Delete event'), ('mark_event_paid', 'Mark paid')),
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
        if event.public:
            actions.append({'name': 'unlist_event', 'style': 'outline'})
        else:  # going live is invite-only (API-025): unpaid events get it disabled with the way to remove the limit
            from console.components.catalog import nearby_action
            actions.append(nearby_action(user, event, style='outline', confirm_text='List it in nearby events for everyone?'))
    if user.is_staff or user.is_superuser:
        actions.append({'name': 'mark_event_paid', 'style': 'outline', 'label': 'Mark unpaid' if event.paid else 'Mark paid',
                        'params': {'paid': '' if event.paid else '1'},
                        'confirm_text': 'Mark this event unpaid?' if event.paid else 'Mark this event paid? The free limit goes.'})
    if role == 'owner':
        actions.append({'name': 'delete_event', 'style': 'danger', 'fields': {'confirm_code': event.event_code},
                        'confirm_text': 'Delete this event permanently? This can’t be undone.'})
    from django.urls import reverse as _r
    from events import free_limit
    limit = free_limit.status(event)
    limit_form = None
    if limit.get('limited') and manage:
        from console.views_events import LimitRequestForm
        limit_form = form_json(LimitRequestForm(), ['message'], action=_r('console:event_limit_request', args=[event.pk]),
                               submit=[{'label': 'Remove the limit', 'style': 'primary'}])
        limit_form['no_ai'] = True
    return {'event_id': event.pk, 'name': f'Status · {event.name}', 'status': event.get_status_display(),
            'public': event.public, 'manage': manage, 'owner': role == 'owner', 'limit': limit, 'limit_form': limit_form,
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
