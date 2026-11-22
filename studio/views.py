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
from credit.decorators import ai_credit_required

from events.display import resolve_display
from events.models import Event
from studio import agent, ai, hosting, importer, publishing
from studio.access import can_publish, can_switch_plan, staff_required, studio_event_view
from studio.assets import AssetError, asset_brief, create_asset, delete_asset, parse_tags
from studio.models import (RESERVED_SLUGS, SLUG_RE, FormSubmission, Site, SiteReport, SiteVersion, StudioAsset,
                           StudioMessage, StudioRun)

MAX_UPLOAD_FILES = 20

def _ctx(event, **extra):
    from console.views_events import _ctx as console_ctx
    return console_ctx(event, 'studio', **extra)


def _site_or_404(event):
    return publishing.ensure_site(event)  # every event may have a website (D1); publishing is gated by plan


def _json_error(message, status=400):
    return JsonResponse({'error': message}, status=status)


# ---------------------------------------------------------------------------
# Editor
# ---------------------------------------------------------------------------

@agent_action('studio_editor', "An event's website (Attnd Studio): the live preview, versions, publish state, address, "
              'assets and form entries. Changes to the site go through design_website.', kind='read')
@studio_event_view
def editor(request, event):
    """The Website page: the same components as the full-screen chat's Website tab (Ask Attnd is the one chat)."""
    publishing.ensure_site(event, request.user)
    return render(request, 'studio/website.html', _ctx(event, studio=True))


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


# ---------------------------------------------------------------------------
# Designing the website (Ask Attnd's design_website) + runs
# ---------------------------------------------------------------------------

def run_task(run):
    """A Studio run as the console's task feed shows it (Ask Attnd's live task card, the tray)."""
    running = run.status in (StudioRun.QUEUED, StudioRun.RUNNING)
    title = ('Bringing your website into Studio' if run.kind == StudioRun.KIND_IMPORT else 'Working on the website')
    steps = [{'text': st.get('done') or st.get('text', ''), 'state': 'done', 'running_text': st.get('text', '')}
             for st in (run.steps or [])][-8:]
    if steps and running:
        steps[-1]['state'] = 'running'
        steps[-1]['text'] = steps[-1]['running_text']  # still counting: "Copied 34 of 52…"
    for st in steps:
        st.pop('running_text', None)
    if run.status == StudioRun.FAILED and steps:
        steps[-1]['state'] = 'failed'
    version = run.result_version.number if run.result_version_id else None
    return {
        'id': f'studio-{run.pk}', 'kind': 'website', 'title': title, 'status': run.status,
        'progress': steps[-1]['text'] if steps and running else ('Waiting to start…' if run.status == StudioRun.QUEUED else ''),
        'summary': (run.reply or (f'Draft version {version} is ready' if version else 'Done'))[:300]
        if run.status == StudioRun.DONE else '',
        'error': run.error if run.status == StudioRun.FAILED else '',
        'url': reverse('studio:editor', args=[run.site.event_id]) if run.status == StudioRun.DONE else None,
        'event_id': run.site.event_id, 'finished_at': run.finished_at.isoformat() if run.finished_at else None,
        'steps': steps or ([{'text': 'Waiting to start…', 'state': 'running'}] if running else []),
        'result_label': 'See the website' if run.status == StudioRun.DONE else '',
        'retry_url': reverse('studio:run_retry', args=[run.site.event_id, run.pk]) if run.status == StudioRun.FAILED else '',
    }


def _budget_error(event):
    if ai.budget_left(event) <= 0:
        return _json_error(f'Today\'s AI budget for this event is used up (${ai.daily_cap(event):.2f} '
                           f'per day). It resets at midnight UTC.', status=429)
    return None


def starter_instruction(event):
    """The first design of a site started from scratch: built from what the event already has."""
    from events.content import models_q_visible_location
    bits = [f'Design a website for "{event.name}"']
    if event.address:
        bits.append(f'at {event.address}')
    text = ' '.join(bits) + '.'
    places = list(event.location_set.filter(hidden=False).values_list('name', flat=True)[:8])
    items = event.scheduleditem_set.filter(hidden=False).filter(models_q_visible_location()).count()
    if event.description:
        text += f' About it: {event.description[:600]}'
    text += (' Pages: a home page with the dates, what it is and how to get the app; the schedule (the data library\'s '
             '<attnd-schedule>), the places with a map (<attnd-locations>, <attnd-map>) and the updates.')
    if places:
        text += f' Places include {", ".join(places)}.'
    if items:
        text += f' There are {items} schedule items.'
    text += ' Use the event\'s colours and logo when it has them. Keep it simple, readable and fast on phones.'
    return text


def _start_design(request, event, text):
    site = _site_or_404(event)
    error = _budget_error(event)
    if error:
        return error
    with transaction.atomic():
        Site.objects.select_for_update().get(pk=site.pk)  # one request at a time per site
        if site.runs.filter(status__in=[StudioRun.QUEUED, StudioRun.RUNNING]).exists():
            return _json_error('Studio is still working on the last request.', status=409)
        run = StudioRun.objects.create(site=site, created_by=request.user, prompt=text)
        StudioMessage.objects.create(site=site, role=StudioMessage.ROLE_USER, text=text, run=run,
                                     created_by=request.user)
        agent.start(run)
    return JsonResponse({'task': run_task(run)}, status=202)


@agent_action('start_website_from_scratch', "Start the event's website from scratch: Studio's designer builds a first "
              'version from the event\'s own details (background; a new draft version).', json=True,
              title='Started a new website')
@studio_event_view
@require_POST
@ai_credit_required
def start_from_scratch(request, event):
    return _start_design(request, event, starter_instruction(event))


@agent_action('design_website', "Change the event's website (Attnd Studio's designer: pages, sections, styles, "
              'images, flyer art, the app look). Runs in the background and makes a new draft version; publishing '
              'is separate (publish_website). Use it for any request about the website ("make the hero bigger", '
              '"add a sponsors page").', json=True,
              params={'instruction': P('string', 'what to change on the website, in the person\'s words')},
              title='Started working on the website')
@studio_event_view
@require_POST
@ai_credit_required
def design(request, event):
    site = _site_or_404(event)
    text = (request.POST.get('instruction') or request.POST.get('text') or '').strip()
    if not text:
        return _json_error('Write what you want to change.')
    if len(text) > 8000:
        return _json_error('That message is too long (8,000 characters at most).')
    return _start_design(request, event, text)


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
        error = (f'Today\'s AI budget for this event is used up (${ai.daily_cap(event):.2f} per day). '
                 f'It resets at midnight UTC.')
    else:
        with transaction.atomic():
            Site.objects.select_for_update().get(pk=site.pk)
            if site.runs.filter(status__in=[StudioRun.QUEUED, StudioRun.RUNNING]).exists():
                error = 'The assistant is still working on the last request.'
            else:
                run = agent.retry(failed, request.user)
    if wants_json:
        return _json_error(error, status=409) if error else JsonResponse({'task': run_task(run)}, status=202)
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
    try:
        return get_object_or_404(SiteVersion, pk=int(version_id), site=site)
    except (TypeError, ValueError):
        raise Http404 from None


@agent_action('restore_website_version', 'Make a copy of an earlier website version the draft.', confirm=True,
              summary=S.studio_restore, params={'version': P('integer', 'the version id')},
              title='Restored a website version')
@studio_event_view
@require_POST
def restore(request, event):
    site = _site_or_404(event)
    version = publishing.restore(site, _version(site, request, request.POST.get('version')), request.user)
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


@agent_action('publish_website', 'Publish the website draft (and its app look and flyer). A site brought in from '
              'another website needs confirm_rights once (the person confirms they may use its content).', confirm=True,
              summary=S.studio_publish, url_name='studio:publish', title='Published the website',
              params={'confirm_rights': P('boolean', 'the person confirmed they own or may use the content', nullable=True)})
@agent_action('publish_website_version', 'Publish one earlier website version.', confirm=True,
              summary=S.studio_publish, url_name='studio:publish_version',
              params={'version': P('integer', 'the version id'),
                      'confirm_rights': P('boolean', 'the person confirmed they own or may use the content', nullable=True)},
              title='Published a website version')
@studio_event_view
@require_POST
def publish(request, event):
    version_id = request.POST.get('version')
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
    blocked = _publish_blocked(request, site, version)
    if blocked:
        return blocked
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


def _publish_blocked(request, site, version):
    """The safeguards before a version goes live (batch 7 §6): rights to an imported site, the quality check's errors,
    the content scan (a hold waits for staff). A redirect with the reason, or None to go ahead."""
    from studio import quality, safety
    back = redirect('studio:editor', event_id=site.event_id)
    if site.review_status == site.REVIEW_HELD:
        messages.info(request, 'Attnd is reviewing this website before it goes live. We’ll email you.')
        return back
    if safety.needs_rights(site):
        if request.POST.get('confirm_rights') not in ('1', 'true', 'on', 'True'):
            messages.error(request, 'This site was brought in from another website. Confirm you have the rights to its '
                                    'text and images (tick the box above Publish), then publish.')
            return back
        safety.confirm_rights(site, request.user)
    report = quality.check(site, version, include_drift=False)
    if report['blocked']:
        messages.error(request, 'Fix these before publishing: ' + ' '.join(
            i['text'] for i in report['issues'] if i['level'] == quality.ERROR)[:600])
        return back
    result = safety.scan(site, version)
    if result.get('verdict') == 'blocked':
        messages.error(request, 'Fix these before publishing: ' + ' '.join(result.get('reasons') or [])[:600])
        return back
    if result.get('verdict') == 'hold' and not result.get('approved_by'):
        safety.hold(site, version, request.user, result.get('reasons') or [])
        messages.info(request, 'Attnd needs to look at this website before it goes live (usually within a day). '
                               'We’ll email you when it’s up.')
        return back
    if site.review_status == site.REVIEW_REJECTED:
        safety.clear(site)  # a new version passed the scan
    return None


@agent_action('fix_website_issues', "Apply the pre-publish check's automatic fixes to the website draft (alt text, "
              'mobile viewport, language, page titles, meta descriptions): a new draft version, no AI.',
              title='Fixed website issues')
@studio_event_view
@require_POST
def fix_issues(request, event):
    from studio import quality
    site = _site_or_404(event)
    if not site.draft:
        messages.info(request, 'Nothing to fix yet.')
        return redirect('studio:editor', event_id=event.pk)
    files, count = quality.autofix(site, site.draft.files)
    if count and files != site.draft.files:
        version = publishing.new_version(site, request.user, files=files, summary=f'Fixed {count} small issues',
                                         source=SiteVersion.SOURCE_CODE)
        messages.success(request, f'Fixed {count} small issue{"s" if count != 1 else ""} (version {version.number}). '
                                  f'Undo goes back.')
    else:
        messages.info(request, 'Nothing to fix automatically.')
    return redirect('studio:editor', event_id=event.pk)


@agent_action('update_website_from_check', "Ask Studio's designer to fix what the pre-publish check found that needs "
              'a person or the AI: text that no longer matches the event (dates, times, the name), out-of-date '
              'translations, broken links, contrast. Background, a new draft version.', json=True,
              title='Started updating the website')
@studio_event_view
@require_POST
@ai_credit_required
def update_from_check(request, event):
    from studio import quality
    site = _site_or_404(event)
    if not site.draft or 'index.html' not in site.draft.files:
        return _json_error('Build the website first.')
    issues = [i for i in quality.check(site, site.draft)['issues'] if i['fix'] not in quality.AUTO_FIXES
              and i['level'] != quality.INFO]
    if not issues:
        return _json_error('The check found nothing to update.')
    lines = '\n'.join(f'- {i["page"] or "site"}: {i["text"]}' for i in issues[:20])
    text = ('Update the website so it matches the event as it is now and fix what the pre-publish check found. Use '
            'the data library (<attnd-schedule>, <attnd-event> …) instead of hard-coded dates and times where you can. '
            f'For translations, translate the changed source pages again and keep their attnd-source meta current.\n{lines}')
    return _start_design(request, event, text)


@agent_action('translate_website', "Add (or refresh) a translated version of the website, kept in sync with the main "
              'pages: pages under /<language code>/ (es, fr, de …). Background, a new draft version.', json=True,
              params={'language': P('string', 'the language code: es, fr, de, it, pt, zh, ja, ko, vi, ar, nl or pl')},
              title='Started a translation')
@studio_event_view
@require_POST
@ai_credit_required
def translate(request, event):
    from studio import quality
    site = _site_or_404(event)
    code = (request.POST.get('language') or '').strip().lower()[:2]
    if code not in quality.LANGS:
        return _json_error('Pick one of: ' + ', '.join(f'{c} ({n})' for c, n in quality.LANGS.items()))
    if not site.draft or 'index.html' not in site.draft.files:
        return _json_error('Build the website first.')
    hashes = '\n'.join(f'- {p}: {quality.source_hash(t)}' for p, t in site.draft.files.items() if p.endswith('.html')
                       and p.split('/', 1)[0] not in quality.LANGS)
    text = (f'Make a {quality.LANGS[code]} version of the website: one page under {code}/ for each main page (same '
            f'design, files and data library; the visible text translated; <html lang="{code}">; links to the other '
            f'{quality.LANGS[code]} pages; a small language switch on every page in both languages). Put '
            f'<meta name="{quality.SOURCE_META}" content="<source page> <hash>"> in each translated page\'s head with '
            f'the hash of its source page from this list, so Attnd can tell when a translation is out of date:\n'
            f'{hashes}')
    return _start_design(request, event, text)


@agent_action('edit_website_text', "Change one piece of text on a website page by hand (no AI): the exact old text and "
              'the new text. A new draft version with Undo.', json=True,
              params={'page': P('string', 'the page file, e.g. index.html'),
                      'old': P('string', 'the exact text on the page now'), 'new': P('string', 'the new text')},
              title='Edited website text')
@studio_event_view
@require_POST
def text_edit(request, event):
    """Quick text edits from the preview (the selection bridge) or the chat: replaces one text run between tags,
    never markup."""
    import html as h
    import re
    site = _site_or_404(event)
    page = (request.POST.get('page') or 'index.html').strip().lstrip('/') or 'index.html'
    if page.endswith('/') or '.' not in page.rsplit('/', 1)[-1]:
        page = (page.rstrip('/') + '/index.html').lstrip('/') if page.strip('/') else 'index.html'
    old = (request.POST.get('old') or '').strip()
    new = (request.POST.get('new') or '').strip()
    files = dict(site.draft.files) if site.draft else {}
    if page not in files:
        return _json_error('That page isn’t on the site.')
    if not old or len(old) > 2000 or len(new) > 2000:
        return _json_error('Pick a piece of text (up to 2,000 characters).')
    if old == new:
        return JsonResponse({'saved': False})
    source = files[page]
    # Only in text between tags (never inside a tag or a script / style).
    pattern = re.compile(r'(>[^<]*?)(' + re.escape(h.escape(old, quote=False)) + '|' + re.escape(old) + r')([^<]*<)')
    skip = [(m.start(), m.end()) for m in re.finditer(r'<(script|style)\b.*?</\1\s*>', source, re.I | re.S)]
    for m in pattern.finditer(source):
        if any(a <= m.start(2) < b for a, b in skip):
            continue
        files[page] = source[:m.start(2)] + h.escape(new, quote=False) + source[m.end(2):]
        break
    else:
        return _json_error('That text wasn’t found as plain text on the page (it may be split by formatting). '
                           'Ask in the chat instead.')
    version = publishing.new_version(site, request.user, files=files, summary=f'Edited text on {page}',
                                     source=SiteVersion.SOURCE_CODE)
    return JsonResponse({'saved': True, 'number': version.number, 'page': page,
                         'undo': reverse('studio:undo', args=[event.pk])})


@agent_action('unpublish_website', 'Take the website offline.', confirm=True, summary=S.studio_unpublish,
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


@agent_action('bring_into_studio', "Build the event's website from its current website: its pages, images and fonts "
              'are copied and Studio\'s designer rebuilds the site (background, a few minutes). Without a url: the '
              "event's own website.", json=True,
              params={'url': P('string', 'the website address'), 'note': P('string', 'notes for the AI', nullable=True)},
              title='Started bringing a website in')
@studio_event_view
@require_POST
@ai_credit_required
def bring_in(request, event):
    site = _site_or_404(event)
    url = (request.POST.get('url') or '').strip() or ((event.source_urls or [''])[0])
    if site.runs.filter(status__in=[StudioRun.QUEUED, StudioRun.RUNNING]).exists():
        return _json_error('Studio is still working on the last request. Try again when it is done.', status=409)
    error = _budget_error(event)
    if error:
        return error
    try:
        run = importer.start_import(site, url, request.user, note=(request.POST.get('note') or '').strip())
    except importer.SiteImportError as exc:
        return _json_error(str(exc))
    return JsonResponse({'task': run_task(run)}, status=202)


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


@agent_action('describe_studio_asset', "Set an asset's description, alt text and tags.", json=True,
              params={'description': P('string', 'description'), 'alt': P('string', 'alt text'),
                      'tags': P('string', 'comma-separated tags')}, title='Described an asset')
@studio_event_view
@require_POST
def asset_action(request, event, asset_id):
    site = _site_or_404(event)
    asset = get_object_or_404(StudioAsset, pk=asset_id, event=event)
    asset.description = (request.POST.get('description') or '')[:1000]
    asset.alt = (request.POST.get('alt') or asset.alt)[:300]
    asset.tags = parse_tags(request.POST.get('tags'))
    asset.save(update_fields=['description', 'alt', 'tags'])
    return JsonResponse({'state': _state(site)})


@agent_action('delete_studio_asset', 'Delete a file from the website\'s asset library.', confirm=True,
              params={'asset': P('string', 'the asset id')}, summary=S.studio_asset_delete, title='Deleted an asset')
@studio_event_view
@require_POST
def asset_delete(request, event):
    import uuid
    site = _site_or_404(event)
    try:
        asset = get_object_or_404(StudioAsset, pk=uuid.UUID(str(request.POST.get('asset'))), event=event)
    except ValueError:
        raise Http404 from None
    if site.versions.filter(flyer_asset=asset).filter(pk__in=[site.draft_id, site.published_id]).exists():
        messages.error(request, 'This file is the flyer of the draft or the live site. Choose another flyer first.')
    else:
        delete_asset(asset)
        messages.info(request, f'Deleted {asset.name}.')
    return redirect('studio:editor', event_id=event.pk)


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
              params={'entry': P('integer', 'the entry id')}, summary=S.studio_entry_delete, title='Deleted a form entry')
@studio_event_view
@require_POST
def form_delete(request, event):
    site = _site_or_404(event)
    FormSubmission.objects.filter(site=site, pk=request.POST.get('entry') or 0).delete()
    messages.info(request, 'Entry deleted.')
    return redirect(reverse('studio:editor', args=[event.pk]) + '#website-forms')


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
@agent_action('approve_held_site', 'Approve a Studio site the content scan held: its version goes live (staff).',
              confirm=True, fixed={'action': 'approve'}, params={'site': P('integer', 'the site id')},
              summary=S.studio_review, title='Approved a held site')
@agent_action('reject_held_site', 'Reject a Studio site the content scan held: it stays offline (staff).',
              confirm=True, fixed={'action': 'reject'}, params={'site': P('integer', 'the site id')},
              summary=S.studio_review, title='Rejected a held site')
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
            site.reports_alerted_at = None  # the next 2 reports alert again
            site.save(update_fields=['reports_alerted_at'])
            messages.info(request, f'Reports for {site.slug} marked resolved.')
        elif action in ('approve', 'reject'):
            from studio import safety
            if not request.user.has_perm('studio.review_site') or site.review_status != Site.REVIEW_HELD:
                raise Http404
            if action == 'approve':
                version = safety.approve(site, request.user)
                messages.success(request, f'{site.slug}: version {version.number if version else "?"} is live.')
            else:
                safety.reject(site, request.user)
                messages.info(request, f'{site.slug} stays offline; the owner was emailed.')
        return redirect('studio:reports')
    open_reports = SiteReport.objects.select_related('site', 'site__event').order_by('-created_at')[:200]
    suspended = Site.objects.filter(suspended=True).select_related('event')
    held = Site.objects.filter(review_status=Site.REVIEW_HELD).select_related('event', 'held_version', 'held_by')
    return render(request, 'studio/reports.html', {'reports': open_reports, 'suspended': suspended, 'held': held})

