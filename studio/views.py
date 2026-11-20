"""The Studio editor and staff pages in the console (spec §8, §11). Every URL 404s without Studio access."""

import csv
import json

from django.conf import settings
from django.contrib import messages
from django.db import transaction
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from agent import summaries as S
from agent.registry import agent_action, param as P

from events.display import resolve_display
from events.models import Event
from studio import agent, ai, hosting, importer, publishing
from studio.access import can_publish, can_switch_plan, staff_required, studio_event_view
from studio.assets import AssetError, asset_brief, create_asset, delete_asset, parse_tags
from studio.models import (RESERVED_SLUGS, SLUG_RE, FormSubmission, Site, SiteReport, SiteVersion, StudioAsset,
                           StudioMessage, StudioRun)

MAX_UPLOAD_FILES = 20
IDEAS = ['Design the site', 'Add a schedule page', 'Add a sponsors section with a sponsorship form',
         'Make it more high-contrast', 'Add a volunteer application form', 'Make flyer art and use it as the flyer']


def _ctx(event, **extra):
    from console.views_events import _ctx as console_ctx
    return console_ctx(event, 'studio', **extra)


def _site_or_404(event):
    if event.plan != Event.PLAN_STUDIO:
        raise Http404
    return publishing.ensure_site(event)


def _json_error(message, status=400):
    return JsonResponse({'error': message}, status=status)


# ---------------------------------------------------------------------------
# Editor
# ---------------------------------------------------------------------------

@agent_action('enable_studio', 'Switch an event to Attnd Studio (owner/admins).', fixed={'action': 'switch'},
              title='Switched to Studio')
@agent_action('studio_editor', "An event's Studio page: the site, versions, chat, assets, forms, settings.",
              kind='read')
@studio_event_view
def editor(request, event):
    if event.plan != Event.PLAN_STUDIO:
        if request.method == 'POST' and request.POST.get('action') == 'switch':
            if not can_switch_plan(event, request.user):
                messages.error(request, 'Only the owner and event admins can switch this event to Studio.')
            else:
                publishing.switch_plan(event, Event.PLAN_STUDIO)
                publishing.ensure_site(event, request.user)
                messages.success(request, 'This event now uses Attnd Studio.')
            return redirect('studio:editor', event_id=event.pk)
        return render(request, 'studio/enable.html', _ctx(event, can_switch=can_switch_plan(event, request.user)))
    site = publishing.ensure_site(event, request.user)
    active = site.runs.filter(status__in=[StudioRun.QUEUED, StudioRun.RUNNING]).first()
    last = site.runs.order_by('-created_at').first()
    forms = _forms_summary(site)
    return render(request, 'studio/editor.html', _ctx(
        event, site=site, can_publish=can_publish(event, request.user),
        is_manager=event.can_manage_team(request.user),
        chat=site.messages.select_related('created_by').order_by('-created_at')[:60][::-1],
        active_run=active, failed_run=last if last and last.status == StudioRun.FAILED else None, forms=forms, usage=ai.usage_summary(event), sites_origin=hosting.sites_origin(),
        public_url=site.public_url(), sources=event.source_urls or [],
        app_preview=_app_preview_html(site), undo=publishing.undo_target(site),
        state=_state(site), ideas=IDEAS,
    ))


def _state(site):
    draft, published = site.draft, site.published
    versions = list(site.versions.select_related('created_by').order_by('-number')[:60])
    assets = list(StudioAsset.objects.filter(event=site.event).order_by('-created_at')[:200])
    origin = hosting.sites_origin()
    return {
        'draft': draft.number if draft else None,
        'published': published.number if published else None,
        'pages': draft.pages if draft else [],
        'files': sorted((draft.files if draft else {}).keys()),
        'flyer': str(draft.flyer_asset_id) if draft and draft.flyer_asset_id else None,
        'preview': hosting.preview_url(site) if origin else '',
        'versions': [{'id': v.pk, 'number': v.number, 'summary': v.summary, 'source': v.get_source_display(),
                      'by': (v.created_by.email if v.created_by else 'AI') if v.source != SiteVersion.SOURCE_AI
                      else 'AI', 'at': v.created_at.isoformat(), 'pages': len(v.pages)} for v in versions],
        'assets': [{**asset_brief(a), 'name': a.name, 'url': f'{origin}/{site.slug}/{a.site_path}' if origin else '',
                    'bytes': a.size} for a in assets],
    }


@agent_action('studio_state', "Studio's state as JSON: draft/published versions, assets, forms.", kind='read')
@studio_event_view
def state(request, event):
    return JsonResponse(_state(_site_or_404(event)))


def _app_preview_html(site):
    """The console's phone preview with the draft's app look applied (in memory only)."""
    from console.views_events import _preview_html
    from events.display import clean_overrides
    event = site.event
    look = (site.draft.app_look if site.draft else {}) or {}
    original = event.accent_color, event.display_config
    if look.get('accent_color'):
        event.accent_color = look['accent_color']
    if look.get('display'):
        event.display_config = clean_overrides({**(event.display_config or {}), **look['display']})
    try:
        resolved = resolve_display(event)
        html = _preview_html(event, resolved)
    finally:
        event.accent_color, event.display_config = original
    return html


@studio_event_view
def app_preview(request, event):
    site = _site_or_404(event)
    look = (site.draft.app_look if site.draft else {}) or {}
    return JsonResponse({'html': _app_preview_html(site), 'logo': publishing.logo_url(site, look),
                         'accent': look.get('accent_color') or event.accent_color or ''})


# ---------------------------------------------------------------------------
# Chat + runs
# ---------------------------------------------------------------------------

def _run_json(run):
    return {'id': run.pk, 'status': run.status, 'kind': run.kind,
            'retry_url': reverse('studio:run_retry', args=[run.site.event_id, run.pk]), 'steps': run.steps[-30:], 'files_rev': run.files_rev,
            'reply': run.reply, 'error': run.error, 'version': run.result_version.number if run.result_version else None,
            'preview': hosting.preview_url(run.site, run=run) if run.is_active and hosting.sites_origin() else '',
            'cost': float(run.cost_usd)}


@agent_action('studio_chat', "Ask Studio's website AI to change the event's website (it works in the background; "
              'follow it with studio_run_status / read_page of the run).', json=True,
              params={'text': P('string', 'what to change on the website')}, title='Asked Studio’s AI')
@studio_event_view
@require_POST
def chat(request, event):
    site = _site_or_404(event)
    text = (request.POST.get('text') or '').strip()
    if not text:
        return _json_error('Write what you want to change.')
    if len(text) > 8000:
        return _json_error('That message is too long (8,000 characters at most).')
    try:
        selection = json.loads(request.POST.get('selection') or '{}')
        selection = {k: str(selection.get(k, ''))[:3000] for k in ('page', 'selector', 'tag', 'text', 'html')
                     } if isinstance(selection, dict) and selection else {}
    except json.JSONDecodeError:
        selection = {}
    if ai.budget_left(event) <= 0:
        return _json_error(f'Today\'s AI budget for this event is used up (${settings.STUDIO_DAILY_SPEND_CAP_USD:.2f} '
                           f'per day). It resets at midnight UTC.', status=429)
    with transaction.atomic():
        Site.objects.select_for_update().get(pk=site.pk)  # one request at a time per site
        if site.runs.filter(status__in=[StudioRun.QUEUED, StudioRun.RUNNING]).exists():
            return _json_error('The assistant is still working on the last request.', status=409)
        run = StudioRun.objects.create(site=site, created_by=request.user, prompt=text, selection=selection)
        StudioMessage.objects.create(site=site, role=StudioMessage.ROLE_USER, text=text, selection=selection,
                                     run=run, created_by=request.user)
        agent.start(run)
    return JsonResponse({'run': _run_json(run)}, status=202)


@agent_action('studio_run_status', 'One Studio AI run: status, steps and reply (JSON).', kind='read')
@studio_event_view
def run_status(request, event, run_id):
    site = _site_or_404(event)
    run = get_object_or_404(StudioRun, pk=run_id, site=site)
    return JsonResponse({'run': _run_json(run)})


@agent_action('retry_studio_run', 'Retry a failed Studio AI run.', title='Retried a Studio run')
@studio_event_view
@require_POST
def run_retry(request, event, run_id):
    """Retry a failed run (an import reuses what it already read and copied)."""
    site = _site_or_404(event)
    failed = get_object_or_404(StudioRun, pk=run_id, site=site, status=StudioRun.FAILED)
    wants_json = 'application/json' in request.headers.get('Accept', '')
    error = None
    if ai.budget_left(event) <= 0:
        error = (f'Today\'s AI budget for this event is used up (${settings.STUDIO_DAILY_SPEND_CAP_USD:.2f} per day). '
                 f'It resets at midnight UTC.')
    else:
        with transaction.atomic():
            Site.objects.select_for_update().get(pk=site.pk)
            if site.runs.filter(status__in=[StudioRun.QUEUED, StudioRun.RUNNING]).exists():
                error = 'The assistant is still working on the last request.'
            else:
                run = agent.retry(failed, request.user)
    if wants_json:
        return _json_error(error, status=409) if error else JsonResponse({'run': _run_json(run)}, status=202)
    if error:
        messages.error(request, error)
    return redirect('studio:editor', event_id=event.pk)


# ---------------------------------------------------------------------------
# Code, versions, publishing
# ---------------------------------------------------------------------------

@agent_action('save_studio_code', "Save the website's files by hand as a new draft version (the whole site).",
              json=True, params={'files': P('string', 'JSON object {"path": "complete file text", …} with every file'),
                                 'summary': P('string', 'what changed', nullable=True)}, title='Saved the site code')
@agent_action('studio_code', "The draft website's files as JSON (?version=N for another version).", kind='read')
@studio_event_view
def code(request, event):
    site = _site_or_404(event)
    if request.method == 'GET':
        version = site.draft
        if request.GET.get('version'):
            version = get_object_or_404(SiteVersion, site=site, number=request.GET['version'])
        return JsonResponse({'number': version.number if version else None, 'files': version.files if version else {}})
    try:
        files = json.loads(request.POST.get('files') or '')
        if not isinstance(files, dict) or not all(isinstance(v, str) for v in files.values()):
            raise ValueError
        files = {agent.check_path(p): t for p, t in files.items()}
        agent.check_files(files)
    except agent.ToolError as exc:
        return _json_error(str(exc))
    except (ValueError, json.JSONDecodeError):
        return _json_error('The files could not be read.')
    if site.draft and files == site.draft.files:
        return JsonResponse({'saved': False, 'number': site.draft.number})
    summary = (request.POST.get('summary') or 'Edited the code').strip()[:200]
    version = publishing.new_version(site, request.user, files=files, summary=summary,
                                     source=SiteVersion.SOURCE_CODE)
    return JsonResponse({'saved': True, 'number': version.number})


def _version(site, request, version_id):
    return get_object_or_404(SiteVersion, pk=version_id, site=site)


@agent_action('restore_studio_version', 'Make a copy of an earlier website version the draft.',
              title='Restored a website version')
@studio_event_view
@require_POST
def restore(request, event, version_id):
    site = _site_or_404(event)
    version = publishing.restore(site, _version(site, request, version_id), request.user)
    messages.success(request, f'Version {version.number} is a copy of the version you restored. It is the draft now.')
    return redirect('studio:editor', event_id=event.pk)


@agent_action('undo_studio', "Undo the website draft's last change.", title='Undid a website change')
@studio_event_view
@require_POST
def undo(request, event):
    site = _site_or_404(event)
    target = publishing.undo_target(site)
    if target is None:
        messages.info(request, 'Nothing to undo.')
    else:
        publishing.restore(site, target, request.user)
        messages.success(request, f'Undone: the draft is back to version {target.number}.')
    return redirect('studio:editor', event_id=event.pk)


@agent_action('publish_studio_site', 'Publish the website draft (and its app look and flyer).', confirm=True,
              summary=S.studio_publish, url_name='studio:publish', title='Published the website')
@agent_action('publish_studio_version', 'Publish one website version.', confirm=True, summary=S.studio_publish,
              url_name='studio:publish_version', title='Published a website version')
@studio_event_view
@require_POST
def publish(request, event, version_id=None):
    site = _site_or_404(event)
    if not can_publish(event, request.user):
        messages.error(request, 'Only the owner and event admins can publish the site.')
        return redirect('studio:editor', event_id=event.pk)
    if site.suspended:
        messages.error(request, 'This site was taken down by Attnd. Contact support.')
        return redirect('studio:editor', event_id=event.pk)
    version = _version(site, request, version_id) if version_id else site.draft
    if version is None or 'index.html' not in version.files:
        messages.error(request, 'The site needs a home page (index.html) before it can be published.')
        return redirect('studio:editor', event_id=event.pk)
    publishing.publish(site, version, request.user)
    if not hosting.sites_origin():
        messages.warning(request, 'Published, but no sites address is configured (SITES_HOST), so nobody can open it yet.')
    else:
        messages.success(request, f'Version {version.number} is live at {site.public_url()}. The app shows the new look '
                                  f'and a Website link within a few seconds.')
    if event.status == Event.STATUS_DRAFT:
        messages.info(request, 'Your event itself is still a draft: the schedule, lineup and map stay empty on the '
                               'site until you publish the event.')
    return redirect('studio:editor', event_id=event.pk)


@agent_action('unpublish_studio_site', 'Take the website offline.', confirm=True, summary=S.studio_unpublish,
              title='Took the website offline')
@studio_event_view
@require_POST
def unpublish(request, event):
    site = _site_or_404(event)
    if not can_publish(event, request.user):
        messages.error(request, 'Only the owner and event admins can unpublish the site.')
    else:
        publishing.unpublish(site)
        messages.info(request, 'The site is offline. Your drafts and versions are kept.')
    return redirect('studio:editor', event_id=event.pk)


@studio_event_view
def version_preview(request, event, version_id):
    site = _site_or_404(event)
    url = hosting.preview_url(site, version=_version(site, request, version_id))
    if not url:
        raise Http404
    return redirect(url)


@agent_action('create_studio_preview_link', 'Make a private preview link of the draft website.', json=True,
              title='Made a preview link')
@studio_event_view
@require_POST
def preview_link(request, event):
    site = _site_or_404(event)
    url = hosting.preview_url(site, seconds=settings.STUDIO_PREVIEW_LINK_DAYS * 86400)
    return JsonResponse({'url': url, 'days': settings.STUDIO_PREVIEW_LINK_DAYS})


@agent_action('studio_site_settings', "The website's address (slug) and form email notifications.", confirm=True,
              summary=S.studio_settings, params={'slug': P('string', 'the website address, 3-50 a-z 0-9 -'),
                                                  'notify_on_forms': P('boolean', 'email the team on form entries')},
              title='Saved the website settings')
@agent_action('reset_studio_preview_links', 'Stop every private preview link shared for the website.',
              confirm=True, summary=S.studio_settings, fixed={'action': 'reset_previews'},
              title='Reset the preview links')
@studio_event_view
@require_POST
def site_settings(request, event):
    site = _site_or_404(event)
    if not event.can_manage_team(request.user):
        messages.error(request, 'Only the owner and event admins can change the site settings.')
        return redirect('studio:editor', event_id=event.pk)
    action = request.POST.get('action')
    if action == 'reset_previews':
        import secrets
        site.preview_secret = secrets.token_hex(16)
        site.save(update_fields=['preview_secret'])
        messages.success(request, 'Every preview link you shared has stopped working.')
    else:
        slug = (request.POST.get('slug') or '').strip().lower()
        if slug != site.slug:
            if not SLUG_RE.match(slug) or slug in RESERVED_SLUGS:
                messages.error(request, 'Addresses use 3–50 lower-case letters, digits and dashes.')
                return redirect('studio:editor', event_id=event.pk)
            if Site.objects.filter(slug=slug).exclude(pk=site.pk).exists():
                messages.error(request, 'That address is taken.')
                return redirect('studio:editor', event_id=event.pk)
            from events import content
            with content.batch_changes(event):  # website_url changes
                site.slug = slug
                site.save(update_fields=['slug'])
        site.notify_on_forms = bool(request.POST.get('notify_on_forms'))
        site.save(update_fields=['notify_on_forms'])
        messages.success(request, 'Site settings saved.')
    return redirect('studio:editor', event_id=event.pk)


@agent_action('bring_site_into_studio', "Bring the organizer's existing website into Studio (AI, background).",
              params={'url': P('string', 'the website address'), 'note': P('string', 'notes for the AI', nullable=True)},
              title='Started bringing a website in')
@studio_event_view
@require_POST
def bring_in(request, event):
    site = _site_or_404(event)
    url = (request.POST.get('url') or '').strip()
    if site.runs.filter(status__in=[StudioRun.QUEUED, StudioRun.RUNNING]).exists():
        messages.error(request, 'The assistant is still working on the last request. Try again when it is done.')
        return redirect('studio:editor', event_id=event.pk)
    try:
        importer.start_import(site, url, request.user, note=(request.POST.get('note') or '').strip())
    except importer.SiteImportError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, 'Bringing your website in. Watch the chat: it takes a few minutes.')
    return redirect('studio:editor', event_id=event.pk)


# ---------------------------------------------------------------------------
# Assets and flyer
# ---------------------------------------------------------------------------

@agent_action('upload_studio_assets', "Add attached files to the website's asset library.", json=True,
              params={'attachment_ids': P('array', 'ids of attached files', items={'type': 'integer'})},
              file_params={'attachment_ids': 'files'}, title='Uploaded assets')
@studio_event_view
@require_POST
def assets_upload(request, event):
    site = _site_or_404(event)
    files = request.FILES.getlist('files')[:MAX_UPLOAD_FILES]
    if not files:
        return _json_error('Choose or drop a file.')
    added, errors = [], []
    for upload in files:
        try:
            data = upload.read(25 * 1024 * 1024 + 1)
            asset = create_asset(event, data, upload.name, user=request.user)
            added.append(str(asset.id))
        except AssetError as exc:
            errors.append(str(exc))
    return JsonResponse({'added': added, 'errors': errors, 'state': _state(site)},
                        status=200 if added or not errors else 400)


@agent_action('delete_studio_asset', 'Delete a file from the asset library.', confirm=True, json=True,
              fixed={'action': 'delete'}, title='Deleted an asset')
@agent_action('describe_studio_asset', "Set an asset's description, alt text and tags.", json=True,
              params={'description': P('string', 'description'), 'alt': P('string', 'alt text'),
                      'tags': P('string', 'comma-separated tags')}, title='Described an asset')
@studio_event_view
@require_POST
def asset_action(request, event, asset_id):
    site = _site_or_404(event)
    asset = get_object_or_404(StudioAsset, pk=asset_id, event=event)
    if request.POST.get('action') == 'delete':
        if site.versions.filter(flyer_asset=asset).filter(pk__in=[site.draft_id, site.published_id]).exists():
            return _json_error('This file is the flyer of the draft or the live site. Choose another flyer first.')
        delete_asset(asset)
    else:
        asset.description = (request.POST.get('description') or '')[:1000]
        asset.alt = (request.POST.get('alt') or asset.alt)[:300]
        asset.tags = parse_tags(request.POST.get('tags'))
        asset.save(update_fields=['description', 'alt', 'tags'])
    return JsonResponse({'state': _state(site)})


@agent_action('set_studio_flyer', "Choose an image or PDF asset as the event's flyer (new draft version).", json=True,
              params={'asset_id': P('string', 'the asset id')}, title='Chose the flyer')
@studio_event_view
@require_POST
def flyer(request, event):
    site = _site_or_404(event)
    asset = get_object_or_404(StudioAsset, pk=request.POST.get('asset_id'), event=event,
                              kind__in=[StudioAsset.KIND_IMAGE, StudioAsset.KIND_PDF])
    version = publishing.new_version(site, request.user, flyer_asset=asset, summary=f'Chose {asset.name} as the flyer')
    return JsonResponse({'number': version.number, 'state': _state(site)})


# ---------------------------------------------------------------------------
# Forms
# ---------------------------------------------------------------------------

def _forms_summary(site):
    from django.db.models import Count, Max
    rows = site.submissions.values('form').annotate(count=Count('id'), last=Max('created_at')).order_by('-last')
    out = []
    for row in rows:
        recent = list(site.submissions.filter(form=row['form'])[:20])
        fields = list(dict.fromkeys(k for s in recent for k in s.data))[:8]
        out.append({**row, 'fields': fields, 'recent': [{'id': s.pk, 'at': s.created_at,
                                                          'values': [s.data.get(f, '') for f in fields]} for s in recent]})
    return out


@agent_action('studio_form_entries', "A website form's entries as CSV.", kind='read')
@studio_event_view
def form_csv(request, event, form_name):
    site = _site_or_404(event)
    submissions = list(site.submissions.filter(form=form_name).order_by('created_at'))
    if not submissions:
        raise Http404
    fields = list(dict.fromkeys(k for s in submissions for k in s.data))
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="{site.slug}-{form_name}.csv"'
    writer = csv.writer(response)
    writer.writerow(['submitted_at', 'page', *fields])
    for s in submissions:
        # Neutralise spreadsheet formulas in what strangers typed (CSV injection).
        values = [str(s.data.get(f, '')) for f in fields]
        writer.writerow([s.created_at.isoformat(), s.page,
                         *[("'" + v) if v[:1] in ('=', '+', '-', '@', '\t', '\r') else v for v in values]])
    return response


@agent_action('delete_studio_form_entry', 'Delete one website form entry.', confirm=True,
              title='Deleted a form entry')
@studio_event_view
@require_POST
def form_delete(request, event, submission_id):
    site = _site_or_404(event)
    FormSubmission.objects.filter(site=site, pk=submission_id).delete()
    messages.info(request, 'Entry deleted.')
    return redirect(reverse('studio:editor', args=[event.pk]) + '#forms')


# ---------------------------------------------------------------------------
# Staff: abuse reports (Studio itself is admins only; the old beta-access page is gone)
# ---------------------------------------------------------------------------

@agent_action('take_down_studio_site', 'Take a reported Studio site down (staff).', confirm=True,
              fixed={'action': 'suspend'}, params={'site': P('integer', 'the site id'),
                                                    'reason': P('string', 'reason', nullable=True)},
              title='Took a site down')
@agent_action('reinstate_studio_site', 'Put a taken-down Studio site back (staff).', fixed={'action': 'reinstate'},
              params={'site': P('integer', 'the site id')}, title='Reinstated a site')
@agent_action('resolve_studio_reports', "Mark a site's open reports resolved (staff).", fixed={'action': 'resolve'},
              params={'site': P('integer', 'the site id')}, title='Resolved reports')
@agent_action('studio_reports', 'Abuse reports about Studio sites and the sites taken down (staff).', kind='read')
@staff_required
def reports(request):
    if request.method == 'POST':
        site = get_object_or_404(Site, pk=request.POST.get('site'))
        action = request.POST.get('action')
        if action == 'suspend':
            publishing.suspend(site, request.POST.get('reason') or 'Taken down after reports')
            messages.success(request, f'{site.slug} is taken down.')
        elif action == 'reinstate':
            publishing.reinstate(site)
            messages.success(request, f'{site.slug} is back online (if it is published).')
        elif action == 'resolve':
            site.reports.filter(status=SiteReport.OPEN).update(status=SiteReport.RESOLVED)
            messages.info(request, f'Reports for {site.slug} marked resolved.')
        return redirect('studio:reports')
    open_reports = SiteReport.objects.select_related('site', 'site__event').order_by('-created_at')[:200]
    suspended = Site.objects.filter(suspended=True).select_related('event')
    return render(request, 'studio/reports.html', {'reports': open_reports, 'suspended': suspended})

