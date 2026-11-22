"""
Organizer console: create events with AI, edit them by hand, review AI refreshes, configure the app.

Every event page runs with the event's time zone active, so datetime fields display and parse in
the organizer's local time.
"""

import json
import logging
import re
from datetime import timedelta
from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.db import transaction
from django.db.models import Count, Q
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django import forms
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST
from attnd.links import join_url, qr_png
from attnd.permissions import SIGNED_IN, declare, requires, signed_in
from console.recent_auth import recent_auth_required, require as require_recent_auth
from agent import summaries as S
from agent.registry import agent_action, param as P
from credit.decorators import ai_credit_required, blocked as credit_blocked

from console.forms.event_forms import (AIImportForm, BlankEventForm, DisplayForm, EventForm, EventThemeForm,
                                       EventThemeSwitchesForm, LocationForm, NotificationEditForm, ThemeGenerateForm,
                                       NotificationForm, DuplicateEventForm, PasteImportForm, RefreshForm, ScheduledItemForm, SourcesForm,
                                       TeamInviteForm)
from events import push
from events.content import mark_locked, restore, save_revision
from events.display import LABELS, clean_overrides, event_stats, resolve_display
from events.layout import CATALOGUE_VERSION as LAYOUT_CATALOGUE
from events.models import (Device, Event, EventInvite, EventMember, EventRevision, Location, Notification,
                           ScheduledItem, events_for_user, generate_event_code, next_send_slot)
from scrape import diff, jobs
from scrape.diff import tz_or_utc
from scrape.models import Change, ChangeSet, IngestJob

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _tz(event):
    return tz_or_utc(event.timezone)


def event_view(view=None, perm='events.change_event'):
    """login_required + load the event (404 unless the person has `perm` on it, attnd.permissions) + activate its
    time zone. `@event_view` = any team member (events.change_event); `@event_view(perm='events.manage_event')` =
    the owner and event admins."""
    if view is None:
        return lambda v: event_view(v, perm=perm)

    @login_required
    @wraps(view)
    def wrapper(request, event_id, *args, **kwargs):
        event = get_object_or_404(Event, pk=event_id)
        if not request.user.has_perm(perm, event):
            raise Http404
        with timezone.override(_tz(event)):
            return view(request, event, *args, **kwargs)
    return declare(perm, scope='event')(wrapper)


def _ctx(event, section, **extra):
    pending = event.changesets.filter(status=ChangeSet.PENDING).count()
    return {'event': event, 'section': section, 'pending_changes': pending, **extra}


def _job_from_form(form, **kwargs):
    upload = form.cleaned_data.get('upload')
    return IngestJob.objects.create(
        urls=form.cleaned_data.get('urls') or [],
        text=form.cleaned_data.get('text', ''),
        instructions=form.cleaned_data.get('instructions', ''),
        upload_name=upload.name[:256] if upload else '',
        upload_type=upload.content_type if upload else '',
        upload_data=upload.read() if upload else None,
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Dashboard & creation
# ---------------------------------------------------------------------------

def _chat_landing(request, event=None):
    """"Chat as my default view": a signed-in landing opens the full-screen chat (not for Ask Attnd's own internal
    requests, nor when the person came back from the chat with ?page=1)."""
    from urllib.parse import urlencode

    from console.models import ConsolePreference
    if (request.method != 'GET' or request.GET.get('page') == '1' or getattr(request, 'agent_run_id', None)
            or not ConsolePreference.chat_first(request.user, ConsolePreference.is_phone(request))):
        return None
    params = {'from': request.path}
    if event is not None:
        params['event'] = event.pk
    return redirect(reverse('console:assistant') + '?' + urlencode(params))


@require_POST
@signed_in
def walkthrough_skip(request):
    """Stage I17: hide the first-run walkthrough for good (the chat's "Skip" button)."""
    from console.models import ConsolePreference
    ConsolePreference.objects.update_or_create(user=request.user, defaults={'walkthrough_done': True})
    if _wants_json(request) or request.headers.get('X-Attnd-Component'):
        return JsonResponse({'saved': True, 'messages': ['Skipped. Ask Attnd anything any time.']})
    return redirect('console:dashboard')


@agent_action('set_chat_default', 'Make the full-screen chat the default view when the person opens the console or '
              'an event (or turn that off).', params={'on': P('boolean', 'true = chat first, false = the pages')},
              title='Changed the default view')
@require_POST
@signed_in
def chat_default(request):
    from console.models import ConsolePreference
    on = request.POST.get('on') in ('1', 'true', 'on', 'True')
    field = 'phone_chat' if ConsolePreference.is_phone(request) else 'chat_default'  # phones have their own (I15)
    ConsolePreference.objects.update_or_create(user=request.user, defaults={field: on})
    messages.success(request, 'The chat is now your default view.' if on else 'Pages are your default view again.')
    from django.utils.http import url_has_allowed_host_and_scheme
    back = request.POST.get('next') or ''
    if not (back.startswith('/console/') and url_has_allowed_host_and_scheme(back, {request.get_host()})):
        back = reverse('console:dashboard')
    event_id = request.POST.get('event_id', '')
    if on:
        target = back if back.startswith(reverse('console:assistant')) else (
            reverse('console:assistant') + (f'?event={event_id}' if event_id.isdigit() else ''))
    else:
        target = leave_chat_target(request, back, event_id)
    if _wants_json(request):
        return JsonResponse({'chat_default': on, 'redirect': target})
    return redirect(target)


def leave_chat_target(request, back, event_id=''):
    """Chat as default switched off (batch 6): out of the full-screen chat to the page it was about (?from= when it is a
    console path, else the event's Overview, else Your events), with ?chat=closed so the page shows the floating
    panel closed and its launcher pulsing. Off on an ordinary page: that page, the same way."""
    from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit
    from agent.registry import off_limits
    parts = urlsplit(back)
    if parts.path.startswith(reverse('console:assistant')):
        query = parse_qs(parts.query)
        source = (query.get('from') or [''])[0]
        chat_event = (query.get('event') or [event_id or ''])[0]
        if source.startswith('/console/') and not off_limits(source) and not source.startswith(reverse('console:assistant')):
            back = source
        elif str(chat_event).isdigit() and events_for_user(request.user).filter(pk=int(chat_event)).exists():
            back = reverse('console:event', args=[int(chat_event)])
        else:
            back = reverse('console:dashboard')
        parts = urlsplit(back)
    query = [(k, v) for k, values in parse_qs(parts.query).items() for v in values if k not in ('chat', 'page')]
    query += [('page', '1'), ('chat', 'closed')]  # page=1: no redirect back into the chat on the way
    return urlunsplit(('', '', parts.path, urlencode(query), ''))


@agent_action('events_list', 'Your events (upcoming and past) with their codes and counts.', kind='read')
@signed_in
def dashboard(request):
    # Onboarding (Nov 21, 2026): what someone typed into the home page's composer before signing up becomes their AI
    # import now (a demo event they own); the job page shows its steps, then the event in the full-screen chat.
    from home import onboarding
    job, error = onboarding.consume(request)
    if job is not None:
        return redirect('console:job', job_id=job.pk)
    if error:
        messages.error(request, error)
    from console.components.catalog import PAST_SHOWN, SECTION_LABELS, annotated_events, sectioned
    running = IngestJob.objects.filter(created_by=request.user, kind=IngestJob.KIND_CREATE,
                                       status__in=[IngestJob.QUEUED, IngestJob.RUNNING])
    preview = request.GET.get('onboarding') == '1' and (request.user.is_staff or request.user.is_superuser)
    has_events = events_for_user(request.user).exists()
    if preview or (not has_events and not running.exists()):
        # Signed in with no events: the home page's hero, composer and gallery ("Try one on your phone first").
        # Staff see it any time with ?onboarding=1 (the top bar's Preview onboarding), without touching their data.
        return render(request, 'console/onboarding.html', onboarding.page_context(request, preview=preview))
    if (landing := _chat_landing(request)) is not None:
        return landing
    # Each event is the event_tile component (console/components); the counts are correlated subqueries.
    sections = [{'key': key, 'label': SECTION_LABELS[key], 'events': events, 'shown': events[:PAST_SHOWN] if key == 'past' else events,
                 'more': events[PAST_SHOWN:] if key == 'past' else [], 'folded': False}  # all open (owner)
                for key, events in sectioned(list(annotated_events(request.user)), request.user)]
    return render(request, 'console/dashboard.html', {'sections': sections, 'running_jobs': running})


@agent_action('create_event_with_ai', 'Create a new event with the AI import from its website(s), an attached '
              'flyer/PDF and/or pasted text. Runs in the background (see background tasks).',
              form=AIImportForm, prefix='ai', fixed={'mode': 'ai'}, json=True, title='Started an AI import')
@agent_action('create_blank_event', 'Create a new, empty draft event.', form=BlankEventForm, prefix='blank',
              fixed={'mode': 'blank'}, title='Created an event')
@agent_action('new_event_page', 'The page for creating an event.', kind='read')
@signed_in
def event_new(request):
    ai_form = AIImportForm(prefix='ai')
    blank_form = BlankEventForm(prefix='blank')
    mode = request.POST.get('mode', 'ai')
    if request.method == 'POST' and mode == 'ai' and (over := credit_blocked(request)):
        return over
    if request.method == 'POST' and mode == 'ai':
        ai_form = AIImportForm(request.POST, request.FILES, prefix='ai')
        if ai_form.is_valid():
            from studio.access import is_staff
            job = _job_from_form(ai_form, kind=IngestJob.KIND_CREATE, trigger=IngestJob.TRIGGER_CONSOLE,
                                 created_by=request.user,
                                 timezone=request.POST.get('browser_tz', '')[:64],
                                 # Attnd Studio (admins only): the website comes into Studio automatically when
                                 # the import is done; the form's checkbox (on by default) is only an opt-out.
                                 studio_import=bool(ai_form.cleaned_data.get('urls') and is_staff(request.user)
                                                    and not (request.POST.get('studio_import_shown')
                                                             and not request.POST.get('studio_import'))))
            jobs.enqueue(job)
            if _wants_json(request):
                return JsonResponse({'task': task_json(job), 'redirect': reverse('console:dashboard')}, status=202)
            return redirect('console:job', job_id=job.pk)
        if _wants_json(request):
            return JsonResponse({'error': ' '.join(e for errs in ai_form.errors.values() for e in errs)}, status=400)
    elif request.method == 'POST' and mode == 'blank':
        with timezone.override(tz_or_utc(request.POST.get('blank-timezone'))):
            blank_form = BlankEventForm(request.POST, prefix='blank')
            if blank_form.is_valid():
                event = blank_form.save(commit=False)
                event.user = request.user
                event.event_code = generate_event_code()
                event.status = Event.STATUS_DRAFT
                event.save()
                messages.success(request, 'Event created. Add locations and a schedule, then publish it.')
                return redirect('console:event', event_id=event.pk)
    return render(request, 'console/event_new.html', {'ai_form': ai_form, 'blank_form': blank_form, 'mode': mode})


@agent_action('import_job', 'An AI import or update job and its progress.', kind='read')
@signed_in
def job_detail(request, job_id):
    job = get_object_or_404(IngestJob, pk=job_id)
    if not (job.created_by_id == request.user.id or (job.event and job.event.can_edit(request.user))):
        raise Http404
    if request.GET.get('format') == 'json':
        data = {'status': job.status, 'progress': job.progress, 'error': job.error}
        if job.status == IngestJob.DONE:
            data['redirect'] = (reverse('console:changeset', args=[job.result['changeset_id']])
                                if job.kind == IngestJob.KIND_REFRESH and job.result.get('pending')
                                else reverse('console:event', args=[job.event_id]))
        return JsonResponse(data)
    if job.status == IngestJob.DONE and job.event_id and job.kind == IngestJob.KIND_CREATE:
        if job.result.get('notes'):
            messages.info(request, f"AI notes: {job.result['notes']}")
        return redirect('console:event', event_id=job.event_id)
    return render(request, 'console/job.html', {'job': job, 'event': job.event})


# ---------------------------------------------------------------------------
# Background tasks (AI imports and updates): a feed for the corner tray and the service worker
# ---------------------------------------------------------------------------

def _wants_json(request):
    return 'application/json' in request.headers.get('Accept', '')


def task_json(job):
    """One background task as the console's task tray shows it."""
    event_name = job.event.name if job.event_id else (job.result or {}).get('event_name') or 'your event'
    if job.kind == IngestJob.KIND_CREATE:
        title = f'Importing {event_name}' if job.status != IngestJob.DONE else f'Imported {event_name}'
    elif job.trigger == IngestJob.TRIGGER_WATCH:
        title = f'Website changed: updating {event_name}'
    elif job.kind == IngestJob.KIND_THEME:
        title = f'Picking colours for {event_name}' if job.status != IngestJob.DONE else f'Picked colours for {event_name}'
    elif job.kind == IngestJob.KIND_DETAILS:
        title = (f'Filling in details for {event_name}' if job.status != IngestJob.DONE
                 else f'Filled in details for {event_name}')
    elif job.trigger == IngestJob.TRIGGER_PASTE:
        title = f'Reading {job.upload_name or "what you pasted"} into {event_name}'
    else:
        title = f'Checking {event_name} for updates'
    result, url = (job.result or {}), None
    summary = ''
    if job.status == IngestJob.DONE:
        if job.kind == IngestJob.KIND_THEME:
            summary = {'site': 'From your website’s colours', 'default': 'Kept Attnd’s colours'}.get(
                result.get('theme_source'), 'New colours are on')
            url = reverse('console:event', args=[job.event_id]) + '#theme'
        elif job.kind == IngestJob.KIND_DETAILS:
            from scrape.detail_fill import done_text
            summary = done_text({'items': result.get('details_items', 0), 'places': result.get('details_places', 0)})
            url = reverse('console:schedule', args=[job.event_id])
        elif job.kind == IngestJob.KIND_REFRESH:
            pending, applied = result.get('pending', 0), result.get('auto_applied', 0)
            summary = ', '.join(filter(None, [
                f'{applied} change{"s" if applied != 1 else ""} applied' if applied else '',
                f'{pending} to review' if pending else ''])) or 'Everything is up to date'
            url = (reverse('console:changeset', args=[result['changeset_id']]) if pending and result.get('changeset_id')
                   else reverse('console:event', args=[job.event_id]))
        else:
            summary = f"{result.get('location_count', 0)} locations, {result.get('item_count', 0)} schedule items"
            url = reverse('console:event', args=[job.event_id]) if job.event_id else None
    return {
        'id': str(job.pk), 'kind': job.kind, 'title': title, 'status': job.status,
        'progress': job.progress or ('Waiting to start…' if job.status == IngestJob.QUEUED else ''),
        'summary': summary, 'error': job.error if job.status == IngestJob.FAILED else '',
        'url': url, 'event_id': job.event_id,
        'finished_at': job.finished_at.isoformat() if job.finished_at else None,
        # Friendly steps (scrape.jobs.Steps) the tray shows when a task is opened; a queued job has one.
        'steps': [{'text': st.get('text', ''), 'state': st.get('state', 'done')} for st in (job.steps or [])]
                 or ([{'text': 'Waiting to start…', 'state': 'running'}] if job.status == IngestJob.QUEUED else []),
        'result_label': ('See the colours' if job.kind == IngestJob.KIND_THEME
                         else 'See the schedule' if job.kind == IngestJob.KIND_DETAILS
                         else f"Review {_plural_changes(result.get('pending', 0))}" if url and '/changes/' in url
                         else 'Open the event') if url else '',
        # A failed task can run again (its sources are kept; a file that was attached has to be attached again).
        'retry_url': (reverse('console:task_retry', args=[job.pk])
                      if job.status == IngestJob.FAILED and _retryable(job) else ''),
    }


def _studio_tasks(user, recent, asked):
    """Studio website runs (design_website, bring_into_studio) as tasks: the person's running and recent ones and
    the ones asked for (only with Studio access, only on events they can edit)."""
    from studio.access import has_studio_access
    if not has_studio_access(user):
        return []
    from studio.models import StudioRun
    from studio.views import run_task
    wanted = Q(status__in=[StudioRun.QUEUED, StudioRun.RUNNING]) | Q(finished_at__gte=recent)
    if asked:
        wanted |= Q(pk__in=asked)
    runs = (StudioRun.objects.filter(created_by=user, site__event__in=events_for_user(user)).filter(wanted)
            .select_related('site', 'result_version').order_by('-created_at')[:10])
    return [run_task(r) for r in runs]


def _plural_changes(n):
    return f'{n} change' + ('' if n == 1 else 's')


def _retryable(job):
    return job.kind in (IngestJob.KIND_CREATE, IngestJob.KIND_REFRESH, IngestJob.KIND_THEME,
                        IngestJob.KIND_DETAILS) and (
        job.urls or job.text or job.kind == IngestJob.KIND_THEME or (job.event_id and job.event.source_urls))


@agent_action('retry_background_task', 'Run a failed background task (an AI import, a check for updates, picking '
              'colours) again with the same sources.', params={'job_id': P('string', 'the task id')}, json=True,
              title='Started it again')
@signed_in
@require_POST
def task_retry(request, job_id):
    job = get_object_or_404(IngestJob, pk=job_id, created_by=request.user, status=IngestJob.FAILED)
    if job.event_id and not job.event.can_edit(request.user):
        raise Http404
    if not _retryable(job):
        return JsonResponse({'error': 'Attach the file again to try once more.'}, status=400)
    again = IngestJob.objects.create(
        kind=job.kind, trigger=job.trigger if job.trigger != IngestJob.TRIGGER_WATCH else IngestJob.TRIGGER_CONSOLE,
        event=job.event, created_by=request.user, urls=job.urls, text=job.text, instructions=job.instructions,
        timezone=job.timezone, studio_import=job.studio_import, testing_only=job.testing_only)
    jobs.enqueue(again)
    return JsonResponse({'task': task_json(again)}, status=202)


@agent_action('background_tasks', 'Running and recent background tasks (AI imports and updates).', kind='read')
@signed_in
def tasks_feed(request):
    """Running background tasks for this person, plus ones that finished in the last 10 minutes; `?ids=` adds
    those tasks of theirs whatever their age (Ask Attnd's task cards in an older chat)."""
    recent = timezone.now() - timedelta(minutes=10)
    visible_events = events_for_user(request.user).values('id')
    mine = Q(created_by=request.user) | Q(event__in=visible_events, trigger=IngestJob.TRIGGER_WATCH)
    wanted = Q(status__in=[IngestJob.QUEUED, IngestJob.RUNNING]) | Q(finished_at__gte=recent)
    import uuid
    asked, asked_runs = [], []
    for raw in (request.GET.get('ids') or '').split(',')[:20]:
        raw = raw.strip()
        if raw.startswith('studio-') and raw[7:].isdigit():
            asked_runs.append(int(raw[7:]))
            continue
        try:
            asked.append(uuid.UUID(raw))
        except ValueError:
            continue
    if asked:
        wanted |= Q(pk__in=asked)
    qs = IngestJob.objects.filter(mine).filter(wanted).select_related('event').order_by('-created_at')[:20 + len(asked)]
    tasks = [task_json(j) for j in qs] + _studio_tasks(request.user, recent, asked_runs)
    return JsonResponse({'tasks': tasks, 'active': sum(t['status'] in ('queued', 'running') for t in tasks)})


@declare(SIGNED_IN)  # static script for signed-in pages
def service_worker(request):
    """The console's service worker, served from /console/ so it can control every console page."""
    from django.contrib.staticfiles import finders
    path = finders.find('console/sw.js')
    with open(path, encoding='utf-8') as f:
        response = HttpResponse(f.read(), content_type='application/javascript')
    response['Service-Worker-Allowed'] = '/console/'
    response['Cache-Control'] = 'no-cache'
    return response


@declare(SIGNED_IN)
def manifest(request):
    from django.templatetags.static import static
    return JsonResponse({
        'name': 'Attnd Console', 'short_name': 'Attnd', 'start_url': '/console/', 'scope': '/console/',
        'display': 'standalone', 'background_color': '#ffffff', 'theme_color': '#431f86',
        'icons': [{'src': static(f'console/icons/icon-{n}.png'), 'sizes': f'{n}x{n}', 'type': 'image/png',
                   'purpose': 'any maskable'} for n in (192, 512)],
    }, content_type='application/manifest+json')


# ---------------------------------------------------------------------------
# Event overview / edit / lifecycle
# ---------------------------------------------------------------------------

@agent_action('event_overview', "An event's overview: status, join code, audience, sources, recent AI runs.",
              kind='read')
@event_view
def event_overview(request, event):
    if (landing := _chat_landing(request, event)) is not None:
        return landing
    # The header, timing, numbers, most liked, AI updates to review and "Live on your phone" are components
    # (console/components: event_header, event_stats, needs_review, live_on_phone); the rest is this page's own.
    stats = event_stats(event)
    sources_form = SourcesForm(instance=event)
    return render(request, 'console/event_overview.html', _ctx(
        event, 'overview', sources_form=sources_form,
        studio_import=_studio_import_run(event, request.user),
        source_pages=list(event.source_pages.all()), can_manage=event.can_manage_team(request.user),
        refresh_form=RefreshForm(),
        recent_jobs=event.ingest_jobs.all()[:5],
        changesets=event.changesets.exclude(status=ChangeSet.EMPTY)[:5],
        display=resolve_display(event, stats),
    ))


def _studio_import_run(event, user):
    """The newest "Bring into Studio" run, for the overview's notice: Studio events, and staff (whose AI imports bring
    the website in automatically). Everyone else follows the chat's task card."""
    from studio.access import is_staff
    from studio.models import StudioRun
    if event.plan != Event.PLAN_STUDIO and not is_staff(user):
        return None
    return (StudioRun.objects.filter(site__event=event, kind=StudioRun.KIND_IMPORT).select_related('site')
            .order_by('-created_at').first())


@agent_action('update_event_details', "Edit an event's details (name, description, times, address, flyer, assistant "
              'details…). Only pass what should change.', form=EventForm, prefill=True, title='Edited event details')
@agent_action('event_details_page', "An event's details form with the current values.", kind='read')
@event_view
def event_edit(request, event):
    # The canvas's cut-down cards post `part`: only their own fields are saved (never a stale copy of the others).
    part = request.POST.get('part') if request.method == 'POST' else None
    if part in ('context', 'flyer'):
        from console.forms.event_forms import EventContextForm, EventFlyerForm
        cls = EventContextForm if part == 'context' else EventFlyerForm
        small = cls(request.POST, request.FILES, instance=event)
        if small.is_valid():
            mark_locked(event, small.changed_data)
            small.save()
            messages.success(request, 'Details for the assistant saved.' if part == 'context' else 'Flyer saved.')
        else:
            for errors in small.errors.values():
                for e in errors:
                    messages.error(request, e)
        return redirect('console:event', event_id=event.pk)
    form = EventForm(request.POST or None, request.FILES or None, instance=event)
    if request.method == 'POST' and form.is_valid():
        mark_locked(event, form.changed_data)
        form.save()
        messages.success(request, 'Event details saved.')
        return redirect('console:event', event_id=event.pk)
    return render(request, 'console/event_edit.html', _ctx(event, 'details', form=form))


@agent_action('publish_event', 'Publish an event so attendees can join it.', confirm=True, summary=S.publish_event,
              fixed={'action': 'publish'}, title='Published the event')
@agent_action('unpublish_event', 'Move an event back to draft (attendees can no longer open it).', confirm=True,
              summary=S.unpublish_event, fixed={'action': 'unpublish'}, title='Moved the event to draft')
@agent_action('archive_event', 'Archive an event (attendees see it as ended).', confirm=True,
              summary=S.archive_event, fixed={'action': 'archive'}, title='Archived the event')
@agent_action('list_event_nearby', 'List an event publicly in "Find nearby events".', confirm=True,
              summary=S.list_event, fixed={'action': 'list'}, title='Listed the event nearby')
@agent_action('unlist_event', 'Stop listing an event nearby (people can still join with the code).',
              fixed={'action': 'unlist'}, title='Unlisted the event')
@event_view
@require_POST
def event_status(request, event):
    if not event.can_manage_team(request.user):
        messages.error(request, 'Only the owner and event admins can publish, unpublish or archive the event.')
        return redirect('console:event', event_id=event.pk)
    action = request.POST.get('action')
    if action == 'publish':
        event.status = Event.STATUS_PUBLISHED
        messages.success(request, 'Published — attendees can now join with the code or QR.')
    elif action == 'unpublish':
        event.status = Event.STATUS_DRAFT
        messages.info(request, 'Moved back to draft. Attendees can no longer open it.')
    elif action == 'archive':
        event.status = Event.STATUS_ARCHIVED
        messages.info(request, 'Archived. Attendees see it as ended.')
    elif action == 'list':
        # Going live is invite-only (owner, Nov 21, 2026): only paid events are listed in nearby search.
        if not (event.paid or request.user.has_perm('events.mark_event_paid')):
            messages.error(request, 'Listing in nearby events comes with going live. Ask us to remove the limit first.')
            return redirect('console:event', event_id=event.pk)
        event.public = True
        messages.success(request, 'Listed — people nearby can now find it in the app.')
    elif action == 'unlist':
        event.public = False
        messages.info(request, 'Unlisted — only people with the code or QR can join.')
    else:
        raise Http404
    event.save(update_fields=['status', 'public'])
    return redirect('console:event', event_id=event.pk)


class UpgradeRequestForm(forms.Form):
    kind = forms.ChoiceField(choices=[('standard', 'Standard'), ('studio', 'Studio')])
    message = forms.CharField(label='Anything we should know?', required=False, max_length=2000,
                              widget=forms.Textarea(attrs={'rows': 3}))


@agent_action('request_upgrade', "Ask Attnd to upgrade an event: kind 'standard' (paid: no phone limit, nearby "
              "listing) or 'studio' (Standard + publishing its website). Shows the price; no payment here: Attnd emails "
              'back and approves it.', form=UpgradeRequestForm, title='Asked for an upgrade')
@event_view
@require_POST
def event_upgrade_request(request, event):
    from platform_admin import plans
    form = UpgradeRequestForm(request.POST)
    back = redirect(request.POST.get('next') if url_has_allowed_host_and_scheme(request.POST.get('next'), None)
                    else reverse('console:event', args=[event.pk]))
    if not event.can_manage_team(request.user) or not form.is_valid():
        if _wants_json(request):
            return JsonResponse({'saved': False, 'error': 'Only the owner and event admins can ask for an upgrade.'},
                                status=403)
        messages.error(request, 'Only the owner and event admins can ask for an upgrade.')
        return back
    kind = form.cleaned_data['kind']
    req, created = plans.request_upgrade(event, request.user, kind, form.cleaned_data['message'])
    text = (f'Thanks! We’ll email you about {plans.LABELS[kind]} ({plans.price_label(kind)}).' if created
            else 'You already asked; we’ll email you.')
    if _wants_json(request):
        return JsonResponse({'saved': True, 'message': text})
    messages.success(request, text)
    return back


class LimitRequestForm(forms.Form):
    """Remove the limit (API-025): a note to the Attnd team, who mark the event paid."""
    message = forms.CharField(label='Anything we should know?', required=False, max_length=2000,
                              widget=forms.Textarea(attrs={'rows': 3}),
                              help_text='Expected attendees, dates, how to reach you. We reply by email.')


@agent_action('request_limit_removal', "Ask the Attnd team to remove an unpaid event's free limit (20 phones) and "
              'take it live. Emails the admins; an admin marks the event paid.', form=LimitRequestForm,
              title='Asked to remove the limit')
@event_view
@require_POST
def event_limit_request(request, event):
    from attnd.ratelimit import RateLimit
    from events import free_limit
    form = LimitRequestForm(request.POST)
    back = redirect('console:event', event_id=event.pk)
    if not free_limit.status(event).get('limited'):
        messages.info(request, 'This event has no limit.')
        return back
    limit = RateLimit('limit_request_user')
    if not form.is_valid() or limit.exceeded(request.user.pk):
        messages.error(request, 'You already asked today; we’ll be in touch by email.')
        return back
    used = free_limit.status(event)
    from platform_admin import plans  # D2: a Standard upgrade request the owner approves in the Platform admin
    note = f'{used["used"]} of {used["limit"]} free spots used.\n\n{form.cleaned_data["message"] or ""}'.strip()
    plans.request_upgrade(event, request.user, 'standard', note)
    limit.hit(request.user.pk)
    messages.success(request, f'Thanks! We’ll email you about Standard ({plans.price_label("standard")}): no phone '
                              'limit and nearby listing.')
    return back


@agent_action('mark_event_paid', 'Staff: mark an event paid (lifts the 20-phone free limit, allows nearby listing) '
              'or back to unpaid.', confirm=True, params={'paid': P('boolean', 'true = paid, false = back to unpaid')},
              summary=S.mark_paid,
              title='Changed whether the event is paid')
@event_view
@require_POST
def event_paid(request, event):
    if not request.user.has_perm('events.mark_event_paid'):
        raise Http404
    paid = str(request.POST.get('paid', '')).lower() in ('1', 'true', 'on', 'yes')
    event.paid = paid
    event.save(update_fields=['paid'])
    messages.success(request, 'Marked paid: the free limit is gone.' if paid else 'Marked unpaid: the free limit applies.')
    return redirect('console:event', event_id=event.pk)


@agent_action('update_website_settings', "An event's source websites and whether Attnd watches them for changes.",
              form=SourcesForm, prefill='console:event', title='Saved the website settings')
@event_view
@require_POST
def event_sources(request, event):
    if not event.can_manage_team(request.user):
        messages.error(request, 'Only the owner and event admins can change the website settings.')
        return redirect('console:event', event_id=event.pk)
    form = SourcesForm(request.POST, instance=event)
    if form.is_valid():
        form.save()
        from scrape import watch
        watch.sync_pages(event)
        messages.success(request, 'Website settings saved.' + (' Attnd will watch your pages for changes.'
                                                                if event.refresh_schedule == Event.REFRESH_WATCH else ''))
    else:
        messages.error(request, 'Please fix the source URLs.')
    return redirect('console:event', event_id=event.pk)


@agent_action('check_for_updates', 'Check the event\'s websites (and optional pasted text or an attached file) for '
              'updates with the AI. Runs in the background; suggestions then wait for review.', form=RefreshForm,
              json=True, title='Started checking for updates')
@event_view
@require_POST
@ai_credit_required
def event_refresh(request, event):
    form = RefreshForm(request.POST, request.FILES)
    error = None
    if not form.is_valid():
        error = ' '.join(e for errs in form.errors.values() for e in errs)
    elif not (event.source_urls or form.cleaned_data.get('text') or form.cleaned_data.get('upload')):
        error = 'Add a source website first (below), or paste text / upload a file.'
    if error:
        if _wants_json(request):
            return JsonResponse({'error': error}, status=400)
        messages.error(request, error)
        return redirect('console:event', event_id=event.pk)
    job = _job_from_form(form, kind=IngestJob.KIND_REFRESH, trigger=IngestJob.TRIGGER_CONSOLE,
                         event=event, created_by=request.user, timezone=event.timezone)
    jobs.enqueue(job)
    if _wants_json(request):
        return JsonResponse({'task': task_json(job)}, status=202)
    return redirect('console:job', job_id=job.pk)


@agent_action('duplicate_event', 'Copy an event as a new draft ("roll over last year\'s festival to 2027"): every '
              'location, schedule item and assistant detail, moved to a new first day (clock times kept), the team '
              'kept. Not copied: notifications, attendees, numbers. Show the event_rollover card first so the person '
              'checks the dates.', form=DuplicateEventForm, title='Copied the event')
@event_view
@require_POST
def event_duplicate(request, event):
    from console import rollover
    form = DuplicateEventForm(request.POST)
    if not form.is_valid():
        messages.error(request, ' '.join(e for errs in form.errors.values() for e in errs))
        return redirect('console:event', event_id=event.pk)
    shift = rollover.shift_for(event, form.cleaned_data['start_date'])
    copy = rollover.duplicate(event, request.user, shift, name=form.cleaned_data['name'],
                              keep_team=form.cleaned_data['keep_team'])
    messages.success(request, f'Made “{copy.name}”, a draft starting {copy.start_time.astimezone(_tz(copy)):%a %b %-d, %Y}.')
    return redirect('console:event', event_id=copy.pk)


@agent_action('import_into_event', 'Read pasted text or an attached file (flyer image, PDF, CSV / Excel schedule, an '
              'email) into an existing event: the AI suggests changes (new and updated locations, schedule items, '
              'details), which wait in Review. Only that source; nothing is removed. Runs in the background.',
              form=PasteImportForm, json=True, title='Started reading it into the event')
@event_view
@require_POST
@ai_credit_required
def event_import(request, event):
    from scrape.tables import to_text
    form = PasteImportForm(request.POST, request.FILES)
    if not form.is_valid():
        error = ' '.join(e for errs in form.errors.values() for e in errs)
        if _wants_json(request):
            return JsonResponse({'error': error}, status=400)
        messages.error(request, error)
        return redirect('console:event', event_id=event.pk)
    upload = form.cleaned_data.get('upload')
    text = (form.cleaned_data.get('text') or '').strip()
    data = upload.read() if upload else None
    table = to_text(upload.name, upload.content_type, data) if upload else None
    if table is not None:  # spreadsheets and text files go to the AI as text
        text = '\n\n'.join(filter(None, [text, f'From {upload.name}:\n{table}']))
        data = None
    job = IngestJob.objects.create(
        kind=IngestJob.KIND_REFRESH, trigger=IngestJob.TRIGGER_PASTE, event=event, created_by=request.user,
        timezone=event.timezone, text=text, instructions=form.cleaned_data.get('instructions') or '',
        upload_name=upload.name[:256] if upload and data else '', upload_type=upload.content_type if data else '',
        upload_data=data)
    jobs.enqueue(job)
    if _wants_json(request):
        return JsonResponse({'task': task_json(job)}, status=202)
    return redirect('console:job', job_id=job.pk)


@agent_action('delete_event', 'Delete an event permanently (owner only).', confirm=True, summary=S.delete_event,
              build=S.build_delete_event, title='Deleted the event')
@event_view
@require_POST
@recent_auth_required
def event_delete(request, event):
    if event.role_for(request.user) != 'owner':
        messages.error(request, 'Only the owner can delete this event.')
        return redirect('console:event', event_id=event.pk)
    if request.POST.get('confirm_code', '').strip().upper() != event.event_code.upper():
        messages.error(request, 'Type the event code to confirm deletion.')
        return redirect('console:event_edit', event_id=event.pk)
    name = event.name
    event.delete()
    messages.success(request, f'Deleted “{name}”.')
    return redirect('console:dashboard')


@event_view
def event_qr(request, event):
    resp = HttpResponse(qr_png(join_url(event), box_size=16), content_type='image/png')
    resp['Cache-Control'] = 'private, max-age=86400'  # the event code never changes; shown on several pages
    if request.GET.get('download'):
        safe = re.sub(r'[^a-zA-Z0-9]+', '_', event.name).strip('_') or 'event'
        resp['Content-Disposition'] = f'attachment; filename="{safe}_QR.png"'
    return resp


@event_view
def event_preview_qr(request, event):
    """API-026: the "Preview on my phone" QR: the join link plus a preview token made for this person (7 days)."""
    from events import preview
    resp = HttpResponse(qr_png(preview.link(event, request.user), box_size=16), content_type='image/png')
    resp['Cache-Control'] = 'private, max-age=3600'
    return resp


@event_view
def event_phones(request, event):
    """JSON for the QR card's live line: "Updated on 31 of 37 phones" and the preview phones (polled while shown)."""
    from events import preview
    return JsonResponse(preview.phones(event))


@agent_action('remove_preview_phone', "Remove one of the event's preview phones (an organizer's phone that sees "
              'changes instantly): it leaves the event and stops previewing.',
              params={'device': P('string', "The preview phone's id (from the Live on your phone card).")},
              title='Removed a preview phone')
@event_view
@require_POST
def event_preview_remove(request, event):
    from events import preview
    removed = preview.remove(event, request.POST.get('device', ''))
    if 'application/json' in request.headers.get('Accept', ''):
        return JsonResponse({'removed': bool(removed)})
    messages.success(request, 'Removed the preview phone.' if removed else 'That phone isn’t previewing any more.')
    return redirect('console:event', event.pk)


@event_view
def event_poster(request, event):
    paper = 'a4' if request.GET.get('paper') == 'a4' else 'letter'
    layout = 'tent' if request.GET.get('layout') == 'tent' else 'poster'
    return render(request, 'console/poster.html', {'event': event, 'join_url': join_url(event), 'paper': paper,
                                                   'layout': layout, 'tent': layout == 'tent'})


EXPORT_KINDS = ('schedule', 'locations', 'notifications', 'metrics')


@event_view
def export_csv(request, event, kind):
    """An event's schedule, locations, notifications or metrics as CSV (formula-safe, like Prospects' export)."""
    import csv
    from prospects.views import csv_safe
    if kind not in EXPORT_KINDS:
        raise Http404
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    safe = re.sub(r'[^a-zA-Z0-9]+', '_', event.name).strip('_') or 'event'
    response['Content-Disposition'] = f'attachment; filename="{safe}_{kind}.csv"'
    response.write('\ufeff')  # Excel reads UTF-8 with a BOM
    out = csv.writer(response)
    tz = _tz(event)
    local = lambda dt: dt.astimezone(tz).strftime('%Y-%m-%d %H:%M') if dt else ''  # noqa: E731
    row = lambda values: out.writerow([csv_safe(v) for v in values])  # noqa: E731
    if kind == 'schedule':
        row(['Name', 'Start', 'End', 'Location', 'Tags', 'Hidden', 'Description'])
        for it in event.scheduleditem_set.select_related('location').order_by('start_time', 'name'):
            row([it.name, local(it.start_time), local(it.end_time), it.location.name if it.location_id else '',
                 ', '.join(it.tags or []), 'yes' if it.hidden else '', it.description])
    elif kind == 'locations':
        row(['Name', 'Category', 'Summary', 'Address', 'Latitude', 'Longitude', 'Tags', 'Website', 'Hidden', 'Description'])
        for loc in event.location_set.order_by('sort_order', 'name'):
            row([loc.name, loc.category, loc.snippet, loc.address, loc.latitude if loc.latitude is not None else '',
                 loc.longitude if loc.longitude is not None else '', ', '.join(loc.tags or []), loc.website,
                 'yes' if loc.hidden else '', loc.description])
    elif kind == 'notifications':
        row(['Title', 'Text', 'Urgent', 'Status', 'Send time', 'Sent at', 'Delivered', 'Failed'])
        for n in event.notification_set.order_by('send_time'):
            row([n.title, n.body, 'yes' if n.is_urgent else '', 'sent' if n.sent else 'scheduled', local(n.send_time),
                 local(n.sent_at), n.delivered_count, n.failed_count])
    else:
        from console.usage import event_usage
        u = event_usage(event, tz)
        row(['Metric', 'Value'])
        for label, value in (('Joined', u['joins']), ('Left', u['leaves']), ('App opens', u['opens']),
                             ('Interactions', u['interactions']), ('Searches', u['searches']),
                             ('Notifications opened', u['notification_opens']), ('Likes (net)', u['net_hearts']),
                             ('iOS interactions', u['platforms']['ios']), ('Android interactions', u['platforms']['android']),
                             ('QR / link opens', u['link_opens']['total'])):
            row([label, value])
        for title, rows in (('Most viewed location', u['top_locations']), ('Most viewed schedule item', u['top_items']),
                            ('Most liked location', u['hearted_locations']), ('Most liked schedule item', u['hearted_items'])):
            for r in rows:
                row([f'{title}: {r["name"]}', r['count']])
    return response


# ---------------------------------------------------------------------------
# Locations
# ---------------------------------------------------------------------------

@agent_action('locations_page', "An event's locations with their ids.", kind='read')
@event_view
def locations(request, event):
    from console.usage import net_hearts
    hearts = net_hearts(event)['location']
    locs = list(event.location_set.annotate(n_items=Count('scheduleditem')).order_by('sort_order', 'name'))
    for loc in locs:
        loc.hearts = hearts.get(loc.pk, 0)
    return render(request, 'console/locations.html', _ctx(event, 'locations', locations=locs))


@agent_action('add_location', 'Add a location (stage, booth, vendor…) to an event.', form=LocationForm,
              url_name='console:location_new', prefill=True, title='Added a location')
@agent_action('edit_location', 'Edit a location. Only pass what should change.', form=LocationForm,
              url_name='console:location_edit', prefill=True, title='Edited a location')
@agent_action('new_location_page', 'The form for a new location.', kind='read', url_name='console:location_new')
@agent_action('location_page', "A location's form with its current values and schedule.", kind='read',
              url_name='console:location_edit')
@event_view
def location_edit(request, event, location_id=None):
    loc = get_object_or_404(Location, pk=location_id, event=event) if location_id else Location(event=event)
    categories = sorted(set(event.location_set.exclude(category='').values_list('category', flat=True)))
    form = LocationForm(request.POST or None, request.FILES or None, instance=loc, categories=categories)
    if request.method == 'POST' and form.is_valid():
        loc = form.save(commit=False)
        if not loc.pk:
            loc.user = request.user
            loc.sort_order = (event.location_set.order_by('-sort_order')
                              .values_list('sort_order', flat=True).first() or 0) + 1
        else:
            mark_locked(loc, form.changed_data)
        loc.save()
        messages.success(request, f'Saved “{loc.name}”.')
        if 'save_add' in request.POST:
            return redirect('console:location_new', event_id=event.pk)
        return redirect('console:locations', event_id=event.pk)
    items = loc.scheduleditem_set.order_by('start_time') if loc.pk else []
    return render(request, 'console/location_edit.html', _ctx(event, 'locations', form=form, location=loc,
                                                               items=items, categories=categories,
                                                               event_tags=_event_tags(event)))


@agent_action('delete_location', 'Delete a location (its schedule items keep no location).', confirm=True,
              summary=S.delete_location, fixed={'action': 'delete'}, title='Deleted a location')
@agent_action('toggle_location_hidden', 'Hide a location from attendees, or show it again.',
              fixed={'action': 'toggle_hidden'}, title='Hid / showed a location')
@agent_action('unlock_location', 'Let AI refreshes update a location\'s hand-edited fields again.',
              fixed={'action': 'unlock'}, title='Unlocked a location')
@agent_action('find_location_image', 'Look for a photo of a location (its venue website, the event\'s pages, Wikimedia '
              'Commons), checked by AI; replaces an image Attnd found before, never one the organizer added.',
              fixed={'action': 'find_image'}, title='Looked for a location photo')
@agent_action('remove_location_image', 'Remove the photo Attnd found for a location; Attnd never looks again for it.',
              fixed={'action': 'remove_image'}, title='Removed a found photo')
@event_view
@require_POST
def location_action(request, event, location_id):
    loc = get_object_or_404(Location, pk=location_id, event=event)
    action = request.POST.get('action')
    if action == 'delete':
        loc.delete()
        messages.success(request, f'Deleted “{loc.name}”. Its schedule items now have no location.')
    elif action == 'toggle_hidden':
        loc.hidden = not loc.hidden
        loc.save(update_fields=['hidden'])
    elif action == 'unlock':
        loc.locked_fields = []
        loc.save(update_fields=['locked_fields'])
        messages.info(request, 'AI refreshes may update this location again.')
    elif action == 'find_image':
        from events import place_images
        if loc.image and not loc.image_auto or not place_images.enabled():
            messages.error(request, 'Attnd only looks for photos of places without an image of your own.'
                           if loc.image else 'Finding photos isn’t available right now.')
        else:
            loc.image_never_refetch = False
            result = place_images.find_for(loc, force=True)
            if result == 'found':
                messages.success(request, f'Found a photo of “{loc.name}”.')
            elif result == 'skipped':
                messages.error(request, 'Attnd has looked for enough photos today. Try again tomorrow.')
            else:
                messages.info(request, f'No good photo of “{loc.name}” found. You can add your own.')
    elif action == 'remove_image' and loc.image_auto:
        loc.image = None
        loc.image_auto = False
        loc.image_credit = loc.image_license = loc.image_source_url = ''
        loc.image_never_refetch = True
        loc.save()
        messages.success(request, f'Removed the photo of “{loc.name}”. Attnd won’t look for another.')
    return redirect(request.POST.get('next') or reverse('console:locations', args=[event.pk]))


@agent_action('reorder_locations', 'Set the order of an event\'s locations in the app.', json=True,
              params={'order': P('array', 'every location id, in the new order', items={'type': 'integer'})},
              build=lambda args, ctx: {'order': ','.join(str(i) for i in args.get('order') or [])},
              title='Reordered the locations')
@event_view
@require_POST
def locations_reorder(request, event):
    ids = [int(i) for i in request.POST.get('order', '').split(',') if i.strip().isdigit()]
    with transaction.atomic():
        from events.content import batch_changes
        with batch_changes(event):
            for i, loc_id in enumerate(ids):
                Location.objects.filter(pk=loc_id, event=event).update(sort_order=i)
    return JsonResponse({'status': 'ok'})


# ---------------------------------------------------------------------------
# Schedule
# ---------------------------------------------------------------------------

@agent_action('schedule_page', "An event's schedule by day, with item ids.", kind='read')
@event_view
def schedule(request, event):
    items = event.scheduleditem_set.select_related('location').order_by('start_time', 'name')
    location_filter = request.GET.get('location')
    if location_filter:
        items = items.filter(location_id=location_filter)
    from console.usage import net_hearts
    hearts = net_hearts(event)['item']
    days = []
    for item in items:
        item.hearts = hearts.get(item.pk, 0)
        day = timezone.localtime(item.start_time).date()
        if not days or days[-1][0] != day:
            days.append((day, []))
        days[-1][1].append(item)
    return render(request, 'console/schedule.html', _ctx(
        event, 'schedule', days=days, count=items.count(),
        locations=event.location_set.order_by('sort_order', 'name'), location_filter=location_filter))


@agent_action('add_schedule_item', 'Add a schedule item (a set, talk, session…) to an event.',
              form=ScheduledItemForm, url_name='console:item_new', prefill=True, title='Added a schedule item')
@agent_action('edit_schedule_item', 'Edit a schedule item. Only pass what should change.', form=ScheduledItemForm,
              url_name='console:item_edit', prefill=True, title='Edited a schedule item')
@agent_action('new_schedule_item_page', 'The form for a new schedule item.', kind='read', url_name='console:item_new')
@agent_action('schedule_item_page', "A schedule item's form with its current values.", kind='read',
              url_name='console:item_edit')
@event_view
def item_edit(request, event, item_id=None):
    item = get_object_or_404(ScheduledItem, pk=item_id, event=event) if item_id else ScheduledItem(event=event)
    initial = {}
    if not item_id:
        if request.GET.get('location'):
            initial['location'] = request.GET['location']
        last = event.scheduleditem_set.order_by('-start_time').first()
        initial['start_time'] = (last.start_time if last else event.start_time)
    form = ScheduledItemForm(request.POST or None, instance=item, event=event, initial=initial)
    if request.method == 'POST' and form.is_valid():
        item = form.save(commit=False)
        if not item.pk:
            item.user = request.user
        else:
            mark_locked(item, form.changed_data)
        item.save()
        messages.success(request, f'Saved “{item.name}”.')
        if 'save_add' in request.POST:
            url = reverse('console:item_new', args=[event.pk])
            if item.location_id:
                url += f'?location={item.location_id}'
            return redirect(url)
        return redirect('console:schedule', event_id=event.pk)
    return render(request, 'console/item_edit.html', _ctx(event, 'schedule', form=form, item=item,
                                                           event_tags=_event_tags(event)))


def _event_tags(event, limit=30):
    from scrape.tags import event_vocabulary
    return event_vocabulary(event)[:limit]


@agent_action('delete_schedule_item', 'Delete a schedule item.', confirm=True, summary=S.delete_item,
              fixed={'action': 'delete'}, title='Deleted a schedule item')
@agent_action('toggle_schedule_item_hidden', 'Hide a schedule item from attendees, or show it again.',
              fixed={'action': 'toggle_hidden'}, title='Hid / showed a schedule item')
@agent_action('unlock_schedule_item', 'Let AI refreshes update a schedule item\'s hand-edited fields again.',
              fixed={'action': 'unlock'}, title='Unlocked a schedule item')
@event_view
@require_POST
def item_action(request, event, item_id):
    item = get_object_or_404(ScheduledItem, pk=item_id, event=event)
    action = request.POST.get('action')
    if action == 'delete':
        item.delete()
        messages.success(request, f'Deleted “{item.name}”.')
    elif action == 'toggle_hidden':
        item.hidden = not item.hidden
        item.save(update_fields=['hidden'])
    elif action == 'unlock':
        item.locked_fields = []
        item.save(update_fields=['locked_fields'])
    return redirect(request.POST.get('next') or reverse('console:schedule', args=[event.pk]))


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------

@agent_action('send_notification', 'Send a notification (push + the app\'s Updates tab) to the attendees now, or '
              'schedule it. Urgent = for cancellations and safety only.', confirm=True, summary=S.send_notification,
              form=NotificationForm, prefill=True, title='Sent / scheduled a notification')
@agent_action('notifications_page', "An event's notifications (sent and scheduled) and how many devices they reach.",
              kind='read')
@event_view
def notifications(request, event):
    form = NotificationForm(request.POST or None, event=event,
                            initial={'send_time': next_send_slot(timezone.now() + timedelta(hours=1))})
    if request.method == 'POST' and form.is_valid():
        n = form.save(commit=False)
        n.event = event
        n.user = request.user
        if form.cleaned_data['when'] == NotificationForm.WHEN_NOW:
            n.send_time = timezone.now()
            n.save()
            if event.status == Event.STATUS_DRAFT:
                messages.warning(request, 'Saved, but the event is a draft — it will send once you publish.')
            else:
                n = push.send_notification(n)
                messages.success(request, f'Sent{" as urgent" if n.is_urgent else ""} to {n.delivered_count} '
                                          'device(s). It also appears in the app’s Updates tab.')
        else:
            n.save()
            messages.success(request, 'Scheduled.')
        return redirect('console:notifications', event_id=event.pk)
    scheduled = list(event.notification_set.filter(sent=False).order_by('send_time'))
    sent = list(event.notification_set.filter(sent=True).order_by('-send_time'))
    counts = push.reach(event)
    return render(request, 'console/notifications.html', _ctx(
        event, 'notifications', form=form, scheduled=scheduled, sent=sent, reach=counts['reach'],
        urgent_reach=counts['urgent_reach'], live_activities=counts['live_activities'],
        urgent_left=push.urgent_sends_left(event)))


@agent_action('send_scheduled_notification', 'Send a scheduled notification now.', confirm=True,
              summary=S.send_scheduled_notification, fixed={'action': 'send'}, title='Sent a scheduled notification')
@agent_action('delete_scheduled_notification', 'Delete a scheduled (not yet sent) notification.', confirm=True,
              summary=S.delete_notification, fixed={'action': 'delete'}, title='Deleted a scheduled notification')
@agent_action('edit_scheduled_notification', "Change a scheduled (not yet sent) notification's title, text or send "
              'time. Pass only what changes.', form=NotificationEditForm, fixed={'action': 'edit'},
              title='Edited a scheduled notification')
@event_view
@require_POST
def notification_action(request, event, notification_id):
    n = get_object_or_404(Notification, pk=notification_id, event=event)
    action = request.POST.get('action')
    if action == 'edit' and not n.sent:
        form = NotificationEditForm(request.POST)
        if not form.is_valid():
            for errors in form.errors.values():
                for e in errors:
                    messages.error(request, e)
            return redirect('console:notifications', event_id=event.pk)
        changed = []
        for name in ('title', 'body', 'send_time'):
            value = form.cleaned_data.get(name)
            if value not in (None, '') and value != getattr(n, name):
                setattr(n, name, value)
                changed.append(name)
        if changed:
            n.save(update_fields=changed)
            messages.success(request, f'Saved “{n.title}”.')
        else:
            messages.info(request, 'Nothing changed.')
    elif action == 'delete' and not n.sent:
        n.delete()
    elif action == 'send' and not n.sent:
        push.send_notification(n)
        messages.success(request, 'Sent.')
    return redirect('console:notifications', event_id=event.pk)


# ---------------------------------------------------------------------------
# Display / app layout
# ---------------------------------------------------------------------------

@agent_action('update_app_layout', "The app's layout for an event: preset, tabs, styles, map options, accent colour. "
              'Only pass what should change.', form=DisplayForm, prefill=True,
              params={'accent_color': P('string', 'Accent colour #RRGGBB', nullable=True),
                      'accent_default': P('boolean', 'true = use the default accent colour', nullable=True)},
              title='Saved the app layout')
@agent_action('app_layout_page', "An event's app layout settings and what the app resolves them to.", kind='read')
@event_view
def display_settings(request, event):
    from events.display import empty_tabs
    overrides = clean_overrides(event.display_config)
    form = DisplayForm(request.POST or None, initial=DisplayForm.initial_from(overrides),
                       empty=empty_tabs(event_stats(event)))
    if request.method == 'POST' and form.is_valid():
        event.display_config = form.to_overrides()
        accent = request.POST.get('accent_color', '')
        event.accent_color = accent if re.fullmatch(r'#[0-9a-fA-F]{6}', accent or '') else ''
        if request.POST.get('accent_default'):
            event.accent_color = ''
        event.save(update_fields=['display_config', 'accent_color'])
        if _wants_json(request):
            return JsonResponse({'saved': True, 'resolved': resolve_display(event, event_stats(event), sections=LAYOUT_CATALOGUE)})
        messages.success(request, 'App layout saved. Open apps update within a few seconds.')
        return redirect('console:display', event_id=event.pk)
    stats = event_stats(event)
    resolved = resolve_display(event, stats, sections=LAYOUT_CATALOGUE)
    if request.method == 'POST' and _wants_json(request):
        return JsonResponse({'saved': False, 'errors': form.errors}, status=400)
    return render(request, 'console/display.html', _ctx(
        event, 'display', form=form, resolved=resolved, stats=stats, labels=LABELS))


# ---------------------------------------------------------------------------
# Screens (API-024): which sections the apps show on each screen
# ---------------------------------------------------------------------------

def _screen_types():
    from events import layout as L
    return '; '.join(f'{screen}: {", ".join(L.CATALOGUE[screen])}' for screen in L.SCREENS)


_SCREEN = P('string', 'The app screen: home (the Home tab), event_info (the event\'s info page), item_detail (every '
            'schedule item), place_detail (every place), map_overlay (the map\'s Now playing strip and pin card), '
            'options_event (the event rows in Options), schedule_tab / places_tab / updates_tab (the tabs\' lists), '
            'onboarding (the welcome cards)', enum=['home', 'event_info', 'item_detail', 'place_detail',
                                                   'map_overlay', 'options_event', 'schedule_tab', 'places_tab',
                                                   'updates_tab', 'onboarding'])
_MATCH = P('string', "Only for one category or tag: 'category:<category>' (places of that category; schedule items "
           "at them) or 'tag:<tag>'. null = the whole screen. Only item_detail and place_detail.", nullable=True)


# ---------------------------------------------------------------------------
# Unpublished changes (batch 6 §4): the rows are the draft; attendees see the last Publish
# ---------------------------------------------------------------------------

@agent_action('unpublished_changes', "What changed in an event since its last Publish (field by field, draft → what "
              "attendees see), and how many phones would get it.", kind='read')
@event_view
def event_changes(request, event):
    if request.GET.get('bar'):  # the sticky bar on every event page (console.js fetches it)
        from console import components as C
        data = C.load(request.user, 'unpublished_changes', {'event_id': event.pk})
        return HttpResponse(C.render('unpublished_changes', data, request, surface='page', part='bar')
                            if data['count'] else '')
    return render(request, 'console/changes_unpublished.html', _ctx(event, 'overview'))


@agent_action('publish_changes', "Publish an event's unpublished changes to the attendees' phones (they see them "
              "within seconds). Notifications and the event's status never wait for this.", confirm=True,
              summary=S.publish_changes, title='Published the changes')
@event_view
@require_POST
def event_publish_changes(request, event):
    from events import drafts
    n = len(drafts.changes(event))
    drafts.publish(event, request.user)
    text = f'Published {n} change{"" if n == 1 else "s"} to the attendees\u2019 phones.' if n else 'Published.'
    if _wants_json(request):
        return JsonResponse({'saved': True, 'message': text})
    messages.success(request, text)
    return redirect(request.POST.get('next') if url_has_allowed_host_and_scheme(request.POST.get('next'), None)
                    else reverse('console:event', args=[event.pk]))


@agent_action('discard_changes', "Throw away an event's unpublished changes: the draft goes back to what attendees "
              "see (undoable).", confirm=True, summary=S.discard_changes, title='Discarded the unpublished changes')
@event_view
@require_POST
def event_discard_changes(request, event):
    from events import drafts
    n = len(drafts.changes(event))
    drafts.discard(event, request.user)
    text = f'Discarded {n} unpublished change{"" if n == 1 else "s"}.'
    if _wants_json(request):
        return JsonResponse({'saved': True, 'message': text})
    messages.success(request, text)
    return redirect(reverse('console:event', args=[event.pk]))


class LiveActivityForm(forms.Form):
    follows = forms.ChoiceField(label='What it follows', widget=forms.RadioSelect, required=False)
    location_id = forms.TypedChoiceField(label='Which place', coerce=int, required=False, empty_value=None)
    buttons = forms.MultipleChoiceField(label='Buttons', widget=forms.CheckboxSelectMultiple, required=False)
    stale_text = forms.CharField(label='When it can\'t update', max_length=60, required=False,
                                 help_text='Shown when the phone hasn\'t had news for a while. Empty = "Open Attnd to update".')

    def __init__(self, *args, event=None, **kwargs):
        from events import live_activity as LA
        super().__init__(*args, **kwargs)
        self.fields['follows'].choices = list(LA.FOLLOWS.items())
        self.fields['buttons'].choices = list(LA.BUTTONS.items())
        self.fields['location_id'].choices = [('', 'Pick a place')] + [
            (loc.pk, loc.name) for loc in event.location_set.order_by('sort_order', 'name')] if event else []


@agent_action('update_live_activity', "What the Lock Screen Live Activity follows by default (my_schedule, everything "
              "or one place: location_id), which buttons it shows (add, skip, map) and its stale text. Urgent "
              "announcements always show (a safety rule).", form=LiveActivityForm, prefill=True,
              title='Saved the Live Activity settings')
@event_view
@require_POST
def event_live_activity(request, event):
    from events import live_activity as LA
    form = LiveActivityForm(request.POST, event=event)
    if not form.is_valid():
        if _wants_json(request):
            return JsonResponse({'saved': False, 'error': 'Check the Live Activity settings.'}, status=400)
        messages.error(request, 'Check the Live Activity settings.')
        return redirect(reverse('console:display', args=[event.pk]) + '#live-activity')
    d = form.cleaned_data
    raw = {'follows': d['follows'] or 'my_schedule', 'location_id': d['location_id'], 'buttons': d['buttons'],
           'stale_text': d['stale_text']}
    event.live_activity = LA.stored(raw, set(event.location_set.values_list('pk', flat=True)))
    event.save(update_fields=['live_activity'])
    if _wants_json(request):
        return JsonResponse({'saved': True, 'message': 'Saved the Live Activity settings.'})
    messages.success(request, 'Saved the Live Activity settings.')
    return redirect(reverse('console:display', args=[event.pk]) + '#live-activity')


@agent_action('update_screen_layout', 'Which sections an app screen shows and in which order (API-024). sections is a '
              'JSON list in order; types not listed are turned off. Each entry: {"type": …, "show": [parts], '
              '"title": "own heading", "limit": 1-20, "when": [conditions]}. Header parts: item_detail time, status, '
              'tags; place_detail image, category, tags, address, distance, now_next; event_info website. Conditions: '
              'before_start, during, after_end, not:after_end, has_plan, model_available, has:<fact>. Types per '
              'screen: ' + _screen_types() + '. The Home tab must also be on in the app layout\'s tabs '
              '(update_app_layout).',
              params={'screen': _SCREEN, 'match': _MATCH,
                      'sections': P('string', 'JSON list of sections in order, e.g. [{"type": "menu"}, '
                                    '{"type": "hours"}, {"type": "header", "show": ["image", "category"]}]')},
              fixed={'mode': 'json'}, title='Saved a screen of the app')
@agent_action('reset_screen_layout', "Put an app screen back to Attnd's automatic layout (undoable).",
              params={'screen': _SCREEN}, fixed={'action': 'reset'}, title='Reset a screen to automatic')
@agent_action('add_screen_override', 'Give the places of one category (or the items / places with one tag) their own '
              'section order on an app screen, starting from the screen\'s order; then change it with '
              'update_screen_layout and the same match.',
              params={'screen': _SCREEN, 'match': P('string', "'category:<category>' or 'tag:<tag>'")},
              fixed={'action': 'add_override'}, title='Added an order for a category or tag')
@agent_action('remove_screen_override', 'Remove the own section order of one category or tag from an app screen.',
              params={'screen': _SCREEN, 'match': P('string', "'category:<category>' or 'tag:<tag>'")},
              fixed={'action': 'remove_override'}, title='Removed an order for a category or tag')
@event_view
def event_screens(request, event):
    import copy
    from console.components.catalog_screens import _subjects, preview
    from events import layout as L
    from events.content import _website_url
    back = reverse('console:display', args=[event.pk]) + '#screens'
    if request.method != 'POST':
        return redirect(back)

    def fail(text):
        if _wants_json(request):
            return JsonResponse({'saved': False, 'error': text}, status=400)
        messages.error(request, text)
        return redirect(back)

    screen = request.POST.get('screen', '')
    if screen not in L.SCREENS:
        return fail('Pick one of the app\'s screens.')
    action = request.POST.get('action') or 'save'
    raw_match = (request.POST.get('match') or '').strip()
    match = L.clean_match(raw_match) if raw_match else ''
    if raw_match and (not match or screen not in ('item_detail', 'place_detail')):
        return fail('A category or tag order works on the schedule item and place screens only, as category:<name> '
                    'or tag:<tag>.')
    locations, items = _subjects(event)
    website = _website_url(event)
    preset = resolve_display(event, event_stats(event, locations), sections=LAYOUT_CATALOGUE)['resolved_preset']
    screens = copy.deepcopy(L.clean_config(event.layout_config).get('screens') or {})
    display = resolve_display(event, event_stats(event, locations), sections=LAYOUT_CATALOGUE)
    current = screens.get(screen) or L.automatic(event, locations, items, preset, website, display)[screen]
    label = L.SCREEN_LABELS[screen]

    def find(spec):
        return next((o for o in spec.get('overrides') or [] if o['match'].lower() == match.lower()), None)

    if action == 'reset':
        if screen not in screens:
            return fail(f'The {label} screen already uses the automatic layout.')
        screens.pop(screen)
        text = f'The {label} screen uses the automatic layout again.'
    elif action in ('add_override', 'remove_override'):
        if not match:
            return fail('Say which category or tag.')
        if action == 'add_override':
            if find(current):
                return fail('That category or tag already has its own order.')
            if len(current.get('overrides') or []) >= L.MAX_OVERRIDES:
                return fail(f'At most {L.MAX_OVERRIDES} orders per screen.')
            current.setdefault('overrides', []).append({'match': match, 'sections': copy.deepcopy(current['sections'])})
            text = f'Added an order for {match.split(":", 1)[1]} on the {label} screen.'
        else:
            if not find(current):
                return fail('That category or tag has no order of its own.')
            current['overrides'] = [o for o in current['overrides'] if o['match'].lower() != match.lower()]
            text = f'Removed the order for {match.split(":", 1)[1]} on the {label} screen.'
        screens[screen] = current
    else:
        target = find(current) if match else current
        if target is None:
            return fail('That category or tag has no order of its own yet: add it first.')
        old = {s['type']: s for s in target['sections']}
        if request.POST.get('mode') == 'json' or 'sections' in request.POST:
            try:
                raw = json.loads(request.POST.get('sections') or '[]')
            except (TypeError, ValueError):
                return fail('sections must be a JSON list.')
            sections = L.clean_sections(raw, screen)
        else:
            on = set(request.POST.getlist('on'))
            order = [t for t in (request.POST.get('order') or '').split(',') if t in L.CATALOGUE[screen]]
            order += [t for t in L.CATALOGUE[screen] if t not in order]
            raw = []
            for t in order:
                if t not in on:
                    continue
                s = {'type': t}
                parts = L.CATALOGUE[screen][t]['parts']
                if parts:
                    shown = [p for p in request.POST.getlist(f'show_{t}') if p in parts]
                    if shown and shown != parts:
                        s['show'] = shown
                title = (request.POST.get(f'title_{t}') or '').strip()
                if title:
                    s['title'] = title
                limit = (request.POST.get(f'limit_{t}') or '').strip()
                if limit.isdigit():
                    s['limit'] = int(limit)
                if t in L.IDS_TYPES:  # catalogue 2: featured / pinned picks
                    s['ids'] = [int(i) for i in request.POST.getlist(f'ids_{t}') if i.isdigit()]
                if t == 'welcome':
                    s['text'] = request.POST.get(f'text_{t}', '')
                    s['image'] = (request.POST.get(f'image_{t}') or '').strip()
                if old.get(t, {}).get('when'):
                    s['when'] = old[t]['when']
                raw.append(s)
            sections = L.clean_sections(raw, screen)
        if not sections:
            return fail('Keep at least one section on the screen.')
        target['sections'] = sections
        screens[screen] = current
        text = f'Saved the {label} screen.' if not match else f'Saved the order for {match.split(":", 1)[1]}.'
    event.layout_config = L.clean_config({'screens': screens})
    event.save(update_fields=['layout_config'])
    if _wants_json(request):
        spec = (event.layout_config.get('screens') or {}).get(screen) or \
            L.automatic(event, locations, items, preset, website, display)[screen]
        shown = (find(spec) or spec) if match else spec
        html = render_to_string('components/_screen_preview.html', {
            'p': preview(event, screen, shown['sections'], locations, items, match, website), 'screen': screen})
        return JsonResponse({'saved': True, 'message': text, 'preview': html})
    messages.success(request, text)
    return redirect(back)


# ---------------------------------------------------------------------------
# Colour theme (API-023)
# ---------------------------------------------------------------------------

def _theme_done(request, event, text):
    if _wants_json(request):
        return JsonResponse({'saved': True, 'message': text})
    messages.success(request, text)
    return redirect(reverse('console:event', args=[event.pk]) + '#theme')


@event_view
def event_theme_css(request, event):
    """The event's theme CSS for console.js to swap into <style data-event-theme> without a reload (empty when off),
    plus the six swatches of the compact Colours card."""
    from events import theme as T
    theme = T.normalize(event.theme) or T.default_theme()
    return JsonResponse({'css': (T.css(event.theme) or '') if event.theme_web else '',
                         'swatches': [theme[m][r] for m in T.MODES for r in T.ROLES]})


@agent_action('update_event_theme', "An event's colour theme as exact #RRGGBB codes: for light and for dark mode a "
              'primary (buttons, active chips, links), a secondary (soft tints) and a tertiary (highlights, hovers). '
              'Pass only what changes. Text contrast is adjusted automatically, so any colour is allowed. Saving '
              'locks the theme against AI changes.', form=EventThemeForm, prefill=True, title='Saved the colours')
@agent_action('reset_event_theme', "Remove an event's custom colours: back to Attnd's purple (undoable).",
              fixed={'action': 'reset'}, title="Went back to Attnd's colours")
@agent_action('event_theme_page', "An event's colour theme: the six colours, contrast notes and the two switches "
              '(website / apps).', kind='read')
@event_view
def event_theme(request, event):
    from events import theme as T
    if request.method == 'POST' and request.POST.get('action') == 'reset':
        event.theme = {}
        event.locked_fields = [f for f in (event.locked_fields or []) if f != 'theme']
        event.save(update_fields=['theme', 'locked_fields'])
        return _theme_done(request, event, 'Back to Attnd’s colours.')
    form = EventThemeForm(request.POST or None, theme=event.theme)
    if request.method == 'POST' and form.is_valid():
        colours = form.to_theme()
        if colours == T.DEFAULT:
            event.theme = {}
        else:
            kept = {k: v for k, v in (T.normalize(event.theme) or {}).items() if k == 'page_colors'}
            event.theme = T.normalize({**colours, **kept, 'source': 'manual',
                                       'made_at': timezone.now().isoformat(timespec='seconds')})
        locked = list(event.locked_fields or [])
        if 'theme' not in locked:
            locked.append('theme')  # a person chose these: later AI work keeps them
        event.locked_fields = locked
        event.save(update_fields=['theme', 'locked_fields'])
        return _theme_done(request, event, 'Saved the colours.' + (
            ' They show on the website.' if event.theme_web and event.theme else ''))
    if request.method == 'POST' and _wants_json(request):
        return JsonResponse({'saved': False, 'errors': form.errors}, status=400)
    return render(request, 'console/event_theme.html', _ctx(event, 'overview', form=form))


@agent_action('set_event_theme_switches', 'Turn the event\'s custom colour theme on or off for the website (console '
              'event pages, join and shared-plan pages) and for the apps, independently. Pass only what changes.',
              form=EventThemeSwitchesForm, prefill='console:event_theme', title='Changed where the colours show')
@event_view
@require_POST
def event_theme_switches(request, event):
    form = EventThemeSwitchesForm(request.POST)
    form.is_valid()  # (booleans: always valid)
    web, apps = bool(form.cleaned_data.get('theme_web')), bool(form.cleaned_data.get('theme_apps'))
    changed = [f for f, v in (('theme_web', web), ('theme_apps', apps)) if getattr(event, f) != v]
    event.theme_web, event.theme_apps = web, apps
    if changed:
        event.save(update_fields=changed)
    where = ' and '.join(filter(None, ['the website' if web else '', 'the apps' if apps else ''])) or 'nowhere'
    return _theme_done(request, event, f'Custom colours: {where}.')


@agent_action('generate_event_theme', "Pick the event's colours with the AI. It uses what the event already has by itself "
              '(its source websites, its Attnd Studio site, logo, flyer and location photos): call it straight away, '
              'never ask for a link or image first; the result says when the event has none of these (then you may '
              'ask). Optional instruction ("warmer", "match our logo", "more contrast"). Runs in the background and '
              'replaces the current colours (undo by setting them back with update_event_theme).',
              form=ThemeGenerateForm, json=True, title='Started picking colours')
@event_view
@require_POST
@ai_credit_required
def event_theme_generate(request, event):
    form = ThemeGenerateForm(request.POST)
    if not form.is_valid():
        if _wants_json(request):
            return JsonResponse({'error': ' '.join(e for errs in form.errors.values() for e in errs)}, status=400)
        return redirect(reverse('console:event', args=[event.pk]) + '#theme')
    job = IngestJob.objects.create(kind=IngestJob.KIND_THEME, trigger=IngestJob.TRIGGER_CONSOLE, event=event,
                                   created_by=request.user, timezone=event.timezone,
                                   instructions=form.cleaned_data.get('instruction', ''))
    jobs.enqueue(job)
    from events.theme_ai import has_sources
    note = ('' if has_sources(event) else 'This event has no website, logo, flyer or photos yet, so the colours come '
            'from its name and description. Add a link or an image to match them.')
    if _wants_json(request):
        return JsonResponse({'task': task_json(job), 'note': note}, status=202)
    messages.info(request, 'Picking colours… they appear here in a few seconds.' + (f' {note}' if note else ''))
    return redirect(reverse('console:event', args=[event.pk]) + '#theme')


@agent_action('fill_in_details', "Read the event's websites again and add \"Details for the assistant\" (performers, "
              'bios, prices, menus, hours, links) to every schedule item and place that has none; never touches what '
              'a person wrote. Runs in the background (a task card).', json=True, title='Started filling in details')
@event_view
@require_POST
@ai_credit_required
def event_fill_details(request, event):
    if not event.source_urls:
        msg = 'This event has no website to read details from. Add its website first.'
        if _wants_json(request):
            return JsonResponse({'error': msg}, status=400)
        messages.error(request, msg)
        return redirect('console:event', event_id=event.pk)
    job = IngestJob.objects.create(kind=IngestJob.KIND_DETAILS, trigger=IngestJob.TRIGGER_CONSOLE, event=event,
                                   created_by=request.user, timezone=event.timezone, urls=list(event.source_urls))
    jobs.enqueue(job)
    if _wants_json(request):
        return JsonResponse({'task': task_json(job)}, status=202)
    messages.info(request, 'Filling in details from the website… it takes a minute or two.')
    return redirect('console:event', event_id=event.pk)


@agent_action('find_music_links', "Look up \"Listen on Apple Music\" links for the event's performers (API-030: only "
              "confident matches; links the organizer set or removed stay). Runs in the background.", json=True,
              title='Looking up music links')
@event_view
@require_POST
def event_music_links(request, event):
    from events import music
    if not music.enabled():
        msg = 'Apple Music links aren\u2019t set up on this server yet.'
        if _wants_json(request):
            return JsonResponse({'error': msg}, status=400)
        messages.error(request, msg)
        return redirect('console:schedule', event_id=event.pk)
    count = sum(len(music.performers(i)) for i in event.scheduleditem_set.all())
    if count:
        import threading
        from django.db import connection

        def run(checked=event):  # the event this view already checked
            try:
                music.refresh_event(checked, force=True)
            except Exception:
                logger.exception('Music links for event %s failed', checked.pk)
            finally:
                connection.close()
        transaction.on_commit(lambda: threading.Thread(target=run, daemon=True, name='music-links-event').start())
    text = (f'Looking up Apple Music links for {count} performer{"" if count == 1 else "s"}\u2026 they appear in a '
            'minute.' if count else 'No performers yet: add them under Details for the assistant on schedule items.')
    if _wants_json(request):
        return JsonResponse({'saved': True, 'message': text})
    messages.info(request, text)
    return redirect('console:schedule', event_id=event.pk)


def _preview_html(event, resolved):
    tz = tz_or_utc(event.timezone)
    locations = list(event.location_set.filter(hidden=False).order_by('sort_order', 'name')[:8])
    items = list(event.scheduleditem_set.filter(hidden=False).select_related('location').order_by('start_time')[:8])
    return render_to_string('console/_phone_preview.html', {
        'event': event, 'd': resolved, 'locations': locations, 'items': items, 'tz': tz,
        'tab_labels': [(t, LABELS['tabs'].get(t, t)) for t in resolved['tabs']],
        'categories': sorted({l.category for l in locations if l.category}),
    })


# ---------------------------------------------------------------------------
# AI changes review
# ---------------------------------------------------------------------------

@agent_action('ai_updates_page', "An event's AI updates (reviews and recent AI jobs).", kind='read')
@event_view
def changesets(request, event):
    sets = event.changesets.exclude(status=ChangeSet.EMPTY)
    return render(request, 'console/changesets.html', _ctx(event, 'changes', changesets=sets,
                                                            jobs=event.ingest_jobs.all()[:10]))


@agent_action('apply_ai_changes', "Apply some of the AI's suggested changes of a review. Fields the organizer "
              'edited by hand ("Your edit") are kept unless replace_my_edits is true (only when they asked to '
              'replace their own edits).', confirm=True, summary=S.apply_changes, build=S.build_apply_changes,
              params={'accept_change_ids': P('array', 'ids of the changes to accept (from the review page)',
                                             items={'type': 'integer'}),
                      'replace_my_edits': P('boolean', 'Also take the AI value for fields edited by hand', nullable=True),
                      'notify_attendees': P('boolean', 'Also tell attendees their app was updated', nullable=True)},
              title='Applied AI suggestions')
@agent_action('discard_ai_changes', 'Discard every pending AI suggestion of a review.', confirm=True,
              summary=S.discard_changes, fixed={'action': 'discard'}, title='Discarded AI suggestions')
@agent_action('ai_changes_review', 'One AI review: the suggested changes with their ids.', kind='read')
@signed_in
def changeset_detail(request, changeset_id):
    cs = get_object_or_404(ChangeSet.objects.select_related('event'), pk=changeset_id)
    event = cs.event
    if not event.can_edit(request.user):
        raise Http404
    with timezone.override(_tz(event)):
        if request.method == 'POST' and cs.status == ChangeSet.PENDING:
            if request.POST.get('action') == 'discard':
                cs.changes.filter(status=Change.PENDING).update(status=Change.REJECTED)
                cs.status = ChangeSet.DISCARDED
                cs.applied_at = timezone.now()
                cs.applied_by = request.user
                cs.save()
                messages.info(request, 'Discarded all suggestions.')
                return redirect('console:changesets', event_id=event.pk)
            decisions = {}
            for ch in cs.changes.filter(status=Change.PENDING):
                if not request.POST.get(f'accept_{ch.id}'):
                    continue
                fields = None
                if ch.op == Change.OP_UPDATE:
                    fields = [f for f in ch.data if request.POST.get(f'field_{ch.id}_{f}')]
                    if not fields:
                        continue
                decisions[ch.id] = {'accept': True, 'fields': fields}
            applied = diff.apply_changeset(cs, decisions, user=request.user)
            if applied and request.POST.get('notify_attendees'):
                event.refresh_from_db()
                push.notify_content_updated(event)
            messages.success(request, f'Applied {applied} change(s). You can undo this from History.')
            return redirect('console:event', event_id=event.pk)

        rows = _changes_for_review(cs)
        return render(request, 'console/changeset.html', _ctx(event, 'changes', cs=cs, rows=rows,
                                                               stale=cs.base_version != event.content_version))


FIELD_LABELS = {'start_time': 'Starts', 'end_time': 'Ends', 'image_url': 'Image', 'latitude': 'Latitude',
                'longitude': 'Longitude', 'snippet': 'Summary', 'timezone': 'Time zone',
                'context': 'Details for the assistant'}


def _fmt(field, value):
    from django.utils.dateparse import parse_datetime
    if value is None or value == '' or value == []:
        return '—'
    if field in ('start_time', 'end_time') and isinstance(value, str):
        dt = parse_datetime(value)
        if dt:
            return timezone.localtime(dt).strftime('%a %b %-d, %-I:%M %p')
    if isinstance(value, list):
        return ', '.join(str(v) for v in value)
    return str(value)


def _changes_for_review(cs):
    rows = []
    # Only the content entities: older ticket drafts (ticketing is parked on git branch parked/tickets) stay hidden.
    changes = cs.changes.filter(entity__in=[Change.ENTITY_EVENT, Change.ENTITY_LOCATION, Change.ENTITY_ITEM])
    for ch in changes:
        fields = []
        kind = {Change.ENTITY_EVENT: 'event', Change.ENTITY_LOCATION: 'location', Change.ENTITY_ITEM: 'item'}.get(ch.entity)
        if ch.op == Change.OP_UPDATE:
            for f, v in ch.data.items():
                old, new = v.get('old'), v.get('new')
                if f == 'location':
                    old, new = v.get('old_name'), v.get('new_name')
                if f == 'context' and kind:  # API-018: a readable summary, not the JSON
                    from events import context as ctx
                    old, new = (ctx.summary(kind, old),
                                ' — '.join(filter(None, [ctx.describe_changes(kind, old, new), ctx.summary(kind, new)])))
                fields.append({'name': f, 'label': FIELD_LABELS.get(f, f.replace('_', ' ').capitalize()),
                               'old': _fmt(f, old), 'new': _fmt(f, new),
                               'conflict': f in ch.conflict_fields, 'is_image': f == 'image_url',
                               'human': f in ch.human_fields})
        elif ch.op == Change.OP_CREATE:
            for f, v in ch.data.items():
                if f in ('location',):
                    continue
                label = 'Location' if f == 'location_name' else FIELD_LABELS.get(f, f.replace('_', ' ').capitalize())
                if f == 'context' and kind:
                    from events import context as ctx
                    v = ctx.summary(kind, v)
                fields.append({'name': f, 'label': label, 'new': _fmt(f, v), 'is_image': f == 'image_url'})
        rows.append({'change': ch, 'fields': fields})
    return rows


# ---------------------------------------------------------------------------
# History & team
# ---------------------------------------------------------------------------

@agent_action('save_snapshot', "Save a snapshot of an event's content in History.", fixed={'action': 'snapshot'},
              params={'summary': P('string', 'what the snapshot is for', nullable=True)}, title='Saved a snapshot')
@agent_action('restore_version', "Restore an event's content (details, locations, schedule) to a History version.",
              confirm=True, summary=S.restore_version,
              params={'revision': P('integer', 'the History revision id')}, title='Restored a version')
@agent_action('history_page', "An event's History versions with their ids.", kind='read')
@event_view
def history(request, event):
    if request.method == 'POST':
        if request.POST.get('action') == 'snapshot':
            save_revision(event, request.POST.get('summary') or 'Manual snapshot', request.user)
            messages.success(request, 'Snapshot saved.')
        else:
            revision = get_object_or_404(EventRevision, pk=request.POST.get('revision'), event=event)
            restore(revision, request.user)
            messages.success(request, f'Restored the version from {timezone.localtime(revision.created_at):%b %-d, %-I:%M %p}.')
        return redirect('console:history', event_id=event.pk)
    revisions = event.revisions.select_related('created_by')[:50]
    return render(request, 'console/history.html', _ctx(event, 'history', revisions=revisions))


@agent_action('invite_team_member', 'Give someone access to an event by email (owner/admins).', confirm=True,
              summary=S.invite, form=TeamInviteForm, fixed={'action': 'invite'}, title='Invited a team member')
@agent_action('cancel_team_invite', 'Cancel a pending team invitation.', confirm=True, summary=S.cancel_invite,
              fixed={'action': 'cancel_invite'}, params={'invite': P('integer', 'the invitation id')},
              title='Cancelled an invitation')
@agent_action('remove_team_member', 'Remove someone from an event\'s team.', confirm=True, summary=S.remove_member,
              fixed={'action': 'remove'}, params={'member': P('integer', 'the team member id')},
              title='Removed a team member')
@agent_action('change_team_role', 'Change a team member\'s role (owner only).', confirm=True, summary=S.change_role,
              fixed={'action': 'role'}, params={'member': P('integer', 'the team member id'),
                                                'role': P('string', 'the new role', enum=['admin', 'contributor'])},
              title='Changed a role')
@agent_action('transfer_event_ownership', 'Make a team member the owner (owner only; you become an admin).',
              confirm=True, summary=S.transfer, fixed={'action': 'transfer'},
              params={'member': P('integer', 'the team member id')}, title='Transferred ownership')
@agent_action('leave_event', 'Leave an event\'s team.', confirm=True, summary=S.leave, fixed={'action': 'leave'},
              title='Left the event')
@agent_action('assign_event_owner', 'Assign an owner to an event by email (site admins only).', confirm=True,
              summary=S.assign_owner, fixed={'action': 'assign_owner'}, audience='superuser',
              permission='events.assign_event_owner',
              params={'email': P('string', 'the new owner\'s email')}, title='Assigned an owner')
@agent_action('team_page', "An event's team, roles and pending invitations, with ids.", kind='read')
@event_view
def team(request, event):
    """Owner + admins + contributors. Invites by email work before the person has an account."""
    from console.accounts import display_name, email_in_use, normalize_email
    role = event.role_for(request.user)
    can_manage = role in ('owner', EventMember.ROLE_ADMIN)
    is_owner = role == 'owner'
    form = TeamInviteForm(request.POST or None)
    back = redirect('console:team', event_id=event.pk)

    if request.method == 'POST':
        action = request.POST.get('action', 'invite')
        if action in ('remove', 'transfer'):  # a sign-in from the last 20 minutes first (console/recent_auth.py)
            if (response := require_recent_auth(request, reverse('console:team', args=[event.pk]))) is not None:
                return response
        member = None
        if request.POST.get('member'):
            member = EventMember.objects.filter(event=event, pk=request.POST['member']).select_related('user').first()

        if action == 'leave' and role in EventMember.ROLE_CHOICES_DICT:
            EventMember.objects.filter(event=event, user=request.user).delete()
            messages.info(request, f'You left {event.name}.')
            return redirect('console:dashboard')

        if action == 'assign_owner' and request.user.has_perm('events.assign_event_owner'):
            email = normalize_email(request.POST.get('email'))
            new_owner = User.objects.filter(email__iexact=email).first()
            if new_owner is None:
                messages.error(request, f'No account uses {email}. They need to sign up first.')
                return back
            _set_owner(event, new_owner)
            messages.success(request, f'{display_name(new_owner)} now owns this event.')
            return back

        if not can_manage:
            messages.error(request, 'Only the owner and event admins can change the team.')
            return back

        if action == 'invite' and form.is_valid():
            email = normalize_email(form.cleaned_data['email'])
            new_role = form.cleaned_data['role'] if is_owner else EventMember.ROLE_CONTRIBUTOR
            user = User.objects.filter(email__iexact=email).first()
            if user is not None and user.id == event.user_id:
                messages.info(request, 'They already own this event.')
            elif user is not None:
                if is_owner:
                    EventMember.objects.update_or_create(event=event, user=user, defaults={'role': new_role})
                else:  # an admin's invite never changes the role of someone already on the team (no demoting admins)
                    EventMember.objects.get_or_create(event=event, user=user, defaults={'role': new_role})
                messages.success(request, f'{email} can now help run this event.')
            elif not is_owner and EventInvite.objects.filter(event=event, email=email).exists():
                messages.info(request, f'{email} is already invited.')
            else:
                EventInvite.objects.update_or_create(event=event, email=email,
                                                     defaults={'role': new_role, 'invited_by': request.user})
                sent = _email_invite(event, email, request.user)
                messages.success(request, f'Invited {email}. {"We emailed them" if sent else "Send them"} the sign-up '
                                          f'link: they get access as soon as they sign up with that email.')
        elif action == 'cancel_invite':
            EventInvite.objects.filter(event=event, pk=request.POST.get('invite')).delete()
            messages.info(request, 'Invitation cancelled.')
        elif action == 'remove' and member and (is_owner or member.role == EventMember.ROLE_CONTRIBUTOR):
            member.delete()
            messages.info(request, f'Removed {display_name(member.user)}.')
        elif action == 'role' and member and is_owner and request.POST.get('role') in dict(EventMember.ROLE_CHOICES):
            member.role = request.POST['role']
            member.save(update_fields=['role'])
            messages.success(request, f'{display_name(member.user)} is now {member.get_role_display().lower()}.')
        elif action == 'transfer' and member and is_owner:
            _set_owner(event, member.user)
            messages.success(request, f'{display_name(member.user)} now owns this event. You are an admin.')
        return back

    return render(request, 'console/team.html', _ctx(
        event, 'team', form=form, role=role, can_manage=can_manage, is_owner=is_owner,
        members=event.members.select_related('user').order_by('role', 'user__email'),
        invites=event.invites.order_by('email'), signup_url=request.build_absolute_uri(reverse('console:signup'))))


def _set_owner(event, new_owner):
    """Make new_owner the owner. The previous owner (if any) stays on the team as an admin."""
    previous = event.user
    with transaction.atomic():
        EventMember.objects.filter(event=event, user=new_owner).delete()
        Event.objects.filter(pk=event.pk).update(user=new_owner)
        if previous is not None and previous.pk != new_owner.pk:
            EventMember.objects.update_or_create(event=event, user=previous,
                                                 defaults={'role': EventMember.ROLE_ADMIN})
    event.user = new_owner


def _email_invite(event, email, inviter):
    """Email the invitation when outgoing mail is configured. Returns True if sent."""
    from django.core.mail import send_mail
    from console.mail import email_enabled, site_url
    if not email_enabled():
        return False
    try:
        send_mail(
            subject=f'You\'re invited to help run {event.name} on Attnd',
            message=(f'{inviter.email} invited you to help manage {event.name} on Attnd.\n\n'
                     f'Sign up with this email address to get access: {site_url(reverse("console:signup"))}\n'
                     f'Already have an account? Sign in with this email: {site_url(reverse("login"))}\n'),
            from_email=None, recipient_list=[email])
        return True
    except Exception:
        logger.exception('Could not email the invitation to %s', email)
        return False


# ---------------------------------------------------------------------------
# Usage (API-005): anonymous counts only
# ---------------------------------------------------------------------------

@agent_action('usage_page', "An event's anonymous usage counts (opens, views, likes).", kind='read')
@event_view
def usage(request, event):
    from console.usage import event_usage
    data = event_usage(event, _tz(event))
    audience = Device.objects.attendees().filter(event=event)
    return render(request, 'console/usage.html', _ctx(event, 'usage', usage=data, in_event_now=audience.count()))


@agent_action('app_usage_page', 'App-wide usage and the support-code lookup (site admins).', kind='read')
@requires('events.view_all_usage')
def admin_usage(request):
    """App-wide usage and support lookup, for site admins only."""
    from console.usage import app_usage, find_devices
    tz = timezone.get_current_timezone()
    code = request.GET.get('code', '').strip()
    return render(request, 'console/admin_usage.html', {
        'usage': app_usage(tz), 'code': code, 'devices': find_devices(code) if code else None,
        'unassigned': Event.objects.filter(user__isnull=True).order_by('-start_time'),
        'section': 'admin_usage',
    })


# ---------------------------------------------------------------------------
# The location form's helpers (page, chat card, canvas overlay): address search and "Fill from this website?"
# ---------------------------------------------------------------------------

@event_view
def place_search(request, event):
    """GET ?q=: places for the address field's Find button (no Google Maps key in the browser, or it failed)."""
    from attnd.ratelimit import RateLimit
    from console import places
    limit = RateLimit('place_search_user')
    if limit.hit(request.user.pk) > limit.limit:
        return JsonResponse({'error': 'Too many searches. Try again in a few minutes.', 'results': []}, status=429)
    near = places._near(event)
    return JsonResponse({'results': places.search(request.GET.get('q', ''), near)})


@require_POST
@event_view
@ai_credit_required
def location_fill(request, event):
    """POST url: suggestions for the location form's empty fields from a website. Saves nothing."""
    from attnd.ratelimit import RateLimit
    from console import places
    limit = RateLimit('location_fill_user')
    if limit.hit(request.user.pk) > limit.limit:
        return JsonResponse({'error': 'You’ve filled many forms from websites. Try again in an hour.'}, status=429)
    try:
        found = places.fill_from_website(event, request.POST.get('url', ''))
    except ValueError as exc:
        return JsonResponse({'error': str(exc)}, status=400)
    logger.info('Location fill for event %s from %s: %d fields, $%s', event.pk, found['host'], len(found['fields']),
                found['cost'])
    return JsonResponse({'fields': found['fields'], 'host': found['host']})
