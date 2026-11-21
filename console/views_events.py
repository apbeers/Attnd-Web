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
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST
from attnd.links import join_url, qr_png
from agent import summaries as S
from agent.registry import agent_action, param as P

from console.forms.event_forms import (AIImportForm, BlankEventForm, DisplayForm, EventForm, LocationForm,
                                       NotificationEditForm,
                                       NotificationForm, RefreshForm, ScheduledItemForm, SourcesForm,
                                       TeamInviteForm)
from events import push
from events.content import mark_locked, restore, save_revision
from events.display import LABELS, clean_overrides, event_stats, resolve_display
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


def event_view(view):
    """login_required + load the event (404 unless the user can edit it) + activate its time zone."""
    @login_required
    @wraps(view)
    def wrapper(request, event_id, *args, **kwargs):
        event = get_object_or_404(Event, pk=event_id)
        if not event.can_edit(request.user):
            raise Http404
        with timezone.override(_tz(event)):
            return view(request, event, *args, **kwargs)
    return wrapper


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
            or not ConsolePreference.chat_first(request.user)):
        return None
    params = {'from': request.path}
    if event is not None:
        params['event'] = event.pk
    return redirect(reverse('console:assistant') + '?' + urlencode(params))


@agent_action('set_chat_default', 'Make the full-screen chat the default view when the person opens the console or '
              'an event (or turn that off).', params={'on': P('boolean', 'true = chat first, false = the pages')},
              title='Changed the default view')
@require_POST
@login_required
def chat_default(request):
    from console.models import ConsolePreference
    on = request.POST.get('on') in ('1', 'true', 'on', 'True')
    ConsolePreference.objects.update_or_create(user=request.user, defaults={'chat_default': on})
    messages.success(request, 'The chat is now your default view.' if on else 'Pages are your default view again.')
    if _wants_json(request):
        return JsonResponse({'chat_default': on})
    from django.utils.http import url_has_allowed_host_and_scheme
    back = request.POST.get('next') or ''
    if not (back.startswith('/console/') and url_has_allowed_host_and_scheme(back, {request.get_host()})):
        back = reverse('console:dashboard')
    if on and not back.startswith(reverse('console:assistant')):
        return redirect(reverse('console:assistant') + (f'?event={request.POST["event_id"]}' if request.POST.get('event_id', '').isdigit() else ''))
    return redirect(back)


@agent_action('events_list', 'Your events (upcoming and past) with their codes and counts.', kind='read')
@login_required
def dashboard(request):
    if (landing := _chat_landing(request)) is not None:
        return landing
    # Each event is the event_tile component (console/components); the counts are correlated subqueries.
    from console.components.catalog import annotated_events, upcoming_and_past
    upcoming, past = upcoming_and_past(list(annotated_events(request.user)))
    running = IngestJob.objects.filter(created_by=request.user, kind=IngestJob.KIND_CREATE,
                                       status__in=[IngestJob.QUEUED, IngestJob.RUNNING])
    return render(request, 'console/dashboard.html', {'upcoming': upcoming, 'past': past, 'running_jobs': running})


@agent_action('create_event_with_ai', 'Create a new event with the AI import from its website(s), an attached '
              'flyer/PDF and/or pasted text. Runs in the background (see background tasks).',
              form=AIImportForm, prefix='ai', fixed={'mode': 'ai'}, json=True, title='Started an AI import')
@agent_action('create_blank_event', 'Create a new, empty draft event.', form=BlankEventForm, prefix='blank',
              fixed={'mode': 'blank'}, title='Created an event')
@agent_action('new_event_page', 'The page for creating an event.', kind='read')
@login_required
def event_new(request):
    ai_form = AIImportForm(prefix='ai')
    blank_form = BlankEventForm(prefix='blank')
    mode = request.POST.get('mode', 'ai')
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
@login_required
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
    else:
        title = f'Checking {event_name} for updates'
    result, url = (job.result or {}), None
    summary = ''
    if job.status == IngestJob.DONE:
        if job.kind == IngestJob.KIND_REFRESH:
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
        'result_label': ('Review the changes' if url and '/changes/' in url else 'Open the event') if url else '',
    }


@agent_action('background_tasks', 'Running and recent background tasks (AI imports and updates).', kind='read')
@login_required
def tasks_feed(request):
    """Running background tasks for this person, plus ones that finished in the last 10 minutes."""
    recent = timezone.now() - timedelta(minutes=10)
    visible_events = events_for_user(request.user).values('id')
    qs = (IngestJob.objects.filter(Q(created_by=request.user) | Q(event__in=visible_events,
                                                                   trigger=IngestJob.TRIGGER_WATCH))
          .filter(Q(status__in=[IngestJob.QUEUED, IngestJob.RUNNING]) | Q(finished_at__gte=recent))
          .select_related('event').order_by('-created_at')[:20])
    tasks = [task_json(j) for j in qs]
    return JsonResponse({'tasks': tasks, 'active': sum(t['status'] in ('queued', 'running') for t in tasks)})


def service_worker(request):
    """The console's service worker, served from /console/ so it can control every console page."""
    from django.contrib.staticfiles import finders
    path = finders.find('console/sw.js')
    with open(path, encoding='utf-8') as f:
        response = HttpResponse(f.read(), content_type='application/javascript')
    response['Service-Worker-Allowed'] = '/console/'
    response['Cache-Control'] = 'no-cache'
    return response


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
    """The newest "Bring into Studio" run, for the overview's notice (Studio beta users only)."""
    if event.plan != Event.PLAN_STUDIO:
        return None
    from studio.access import has_studio_access
    from studio.models import StudioRun
    if not has_studio_access(user):
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
        event.public = True
        messages.success(request, 'Listed — people nearby can now find it in the app.')
    elif action == 'unlist':
        event.public = False
        messages.info(request, 'Unlisted — only people with the code or QR can join.')
    else:
        raise Http404
    event.save(update_fields=['status', 'public'])
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


@agent_action('delete_event', 'Delete an event permanently (owner only).', confirm=True, summary=S.delete_event,
              build=S.build_delete_event, title='Deleted the event')
@event_view
@require_POST
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
    devices = Device.objects.real().filter(event=event)
    reach = devices.filter(subscribed=True).exclude(push_token='').count()
    urgent_reach = devices.exclude(push_token='').count()
    live_activities = devices.filter(platform='ios').exclude(live_activity_token='').count()
    return render(request, 'console/notifications.html', _ctx(
        event, 'notifications', form=form, scheduled=scheduled, sent=sent, reach=reach, urgent_reach=urgent_reach,
        live_activities=live_activities, urgent_left=push.urgent_sends_left(event)))


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
    overrides = clean_overrides(event.display_config)
    form = DisplayForm(request.POST or None, initial=DisplayForm.initial_from(overrides))
    if request.method == 'POST' and form.is_valid():
        event.display_config = form.to_overrides()
        accent = request.POST.get('accent_color', '')
        event.accent_color = accent if re.fullmatch(r'#[0-9a-fA-F]{6}', accent or '') else ''
        if request.POST.get('accent_default'):
            event.accent_color = ''
        event.save(update_fields=['display_config', 'accent_color'])
        if _wants_json(request):
            return JsonResponse({'saved': True, 'resolved': resolve_display(event, event_stats(event))})
        messages.success(request, 'App layout saved. Open apps update within a few seconds.')
        return redirect('console:display', event_id=event.pk)
    stats = event_stats(event)
    resolved = resolve_display(event, stats)
    if request.method == 'POST' and _wants_json(request):
        return JsonResponse({'saved': False, 'errors': form.errors}, status=400)
    return render(request, 'console/display.html', _ctx(
        event, 'display', form=form, resolved=resolved, stats=stats, labels=LABELS))


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


@agent_action('apply_ai_changes', 'Apply some of the AI\'s suggested changes of a review (all fields of each '
              'accepted change).', confirm=True, summary=S.apply_changes, build=S.build_apply_changes,
              params={'accept_change_ids': P('array', 'ids of the changes to accept (from the review page)',
                                             items={'type': 'integer'}),
                      'notify_attendees': P('boolean', 'Also tell attendees their app was updated', nullable=True)},
              title='Applied AI suggestions')
@agent_action('discard_ai_changes', 'Discard every pending AI suggestion of a review.', confirm=True,
              summary=S.discard_changes, fixed={'action': 'discard'}, title='Discarded AI suggestions')
@agent_action('ai_changes_review', 'One AI review: the suggested changes with their ids.', kind='read')
@login_required
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
        member = None
        if request.POST.get('member'):
            member = EventMember.objects.filter(event=event, pk=request.POST['member']).select_related('user').first()

        if action == 'leave' and role in EventMember.ROLE_CHOICES_DICT:
            EventMember.objects.filter(event=event, user=request.user).delete()
            messages.info(request, f'You left {event.name}.')
            return redirect('console:dashboard')

        if action == 'assign_owner' and request.user.is_superuser:
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
                EventMember.objects.update_or_create(event=event, user=user, defaults={'role': new_role})
                messages.success(request, f'{email} can now help run this event.')
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
    audience = Device.objects.real().filter(event=event)
    return render(request, 'console/usage.html', _ctx(event, 'usage', usage=data, in_event_now=audience.count()))


@agent_action('app_usage_page', 'App-wide usage and the support-code lookup (site admins).', kind='read')
@login_required
def admin_usage(request):
    """App-wide usage and support lookup, for site admins only."""
    if not request.user.is_superuser:
        raise Http404
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
