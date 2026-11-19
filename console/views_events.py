"""
Organizer console: create events with AI, edit them by hand, review AI refreshes, configure the app.

Every event page runs with the event's time zone active, so datetime fields display and parse in
the organizer's local time.
"""

import io
import json
import logging
import re
from datetime import timedelta
from functools import wraps
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import qrcode
from django.conf import settings
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

from console.forms.event_forms import (AIImportForm, BlankEventForm, DisplayForm, EventForm, LocationForm,
                                       NotificationForm, RefreshForm, ScheduledItemForm, SourcesForm,
                                       TeamInviteForm)
from events import push
from events.content import mark_locked, restore, save_revision
from events.display import LABELS, clean_overrides, event_stats, resolve_display
from events.models import (CI_DEVICE_PREFIX, Device, Event, EventInvite, EventMember, EventRevision, Location, Notification,
                           ScheduledItem, events_for_user, generate_event_code, next_send_slot)
from scrape import diff, jobs
from scrape.models import Change, ChangeSet, IngestJob

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _tz(event):
    try:
        return ZoneInfo(event.timezone or 'UTC')
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo('UTC')


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

@login_required
def dashboard(request):
    events = (events_for_user(request.user)
              .annotate(n_locations=Count('location', distinct=True),
                        n_items=Count('scheduleditem', distinct=True),
                        n_devices=Count('device', filter=~Q(device__id__startswith=CI_DEVICE_PREFIX), distinct=True),
                        n_pending=Count('changesets', filter=Q(changesets__status=ChangeSet.PENDING), distinct=True))
              .order_by('-start_time'))
    now = timezone.now()
    upcoming = [e for e in events if (e.end_time or e.start_time) >= now - timedelta(days=1)]
    past = [e for e in events if e not in upcoming]
    running = IngestJob.objects.filter(created_by=request.user, kind=IngestJob.KIND_CREATE,
                                       status__in=[IngestJob.QUEUED, IngestJob.RUNNING])
    return render(request, 'console/dashboard.html', {'upcoming': upcoming, 'past': past, 'running_jobs': running})


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
        tz_name = request.POST.get('blank-timezone') or 'UTC'
        try:
            tz = ZoneInfo(tz_name)
        except (ZoneInfoNotFoundError, ValueError):
            tz = ZoneInfo('UTC')
        with timezone.override(tz):
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


@login_required
def job_detail(request, job_id):
    job = get_object_or_404(IngestJob, pk=job_id)
    if not (job.created_by_id == request.user.id or (job.event and job.event.can_edit(request.user))):
        raise Http404
    if request.GET.get('format') == 'json':
        data = {'status': job.status, 'progress': job.progress, 'error': job.error}
        if job.status == IngestJob.DONE:
            data['redirect'] = (reverse('console:changeset', args=[job.result['changeset_id']])
                                if job.kind in (IngestJob.KIND_REFRESH, IngestJob.KIND_TICKETS)
                                and job.result.get('pending')
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
    elif job.kind == IngestJob.KIND_TICKETS:
        title = f'Drafting tickets for {event_name}'
    elif job.trigger == IngestJob.TRIGGER_WATCH:
        title = f'Website changed: updating {event_name}'
    else:
        title = f'Checking {event_name} for updates'
    result, url = (job.result or {}), None
    summary = ''
    if job.status == IngestJob.DONE:
        if job.kind in (IngestJob.KIND_REFRESH, IngestJob.KIND_TICKETS):
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
    }


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

@event_view
def event_overview(request, event):
    stats = event_stats(event)
    devices = Device.objects.real().filter(event=event)
    audience = {
        'joined': devices.count(),
        'subscribed': devices.filter(subscribed=True).exclude(push_token='').count(),
        'active_today': devices.filter(last_seen__gte=timezone.now() - timedelta(hours=24)).count(),
        'ios': devices.filter(platform='ios').count(),
        'android': devices.filter(platform='android').count(),
    }
    sources_form = SourcesForm(instance=event)
    from console.usage import most_hearted
    return render(request, 'console/event_overview.html', _ctx(
        event, 'overview', most_hearted=most_hearted(event), stats=stats, audience=audience, sources_form=sources_form,
        studio_import=_studio_import_run(event, request.user),
        source_pages=list(event.source_pages.all()), can_manage=event.can_manage_team(request.user),
        refresh_form=RefreshForm(),
        recent_jobs=event.ingest_jobs.all()[:5],
        changesets=event.changesets.exclude(status=ChangeSet.EMPTY)[:5],
        display=resolve_display(event, stats),
        join_url=f'https://attnd.app/join/{event.event_code}/',
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


@event_view
def event_edit(request, event):
    form = EventForm(request.POST or None, request.FILES or None, instance=event)
    if request.method == 'POST' and form.is_valid():
        mark_locked(event, form.changed_data)
        form.save()
        messages.success(request, 'Event details saved.')
        return redirect('console:event', event_id=event.pk)
    return render(request, 'console/event_edit.html', _ctx(event, 'details', form=form))


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
    img = qrcode.make(f'https://attnd.app/join/{event.event_code}/', box_size=16, border=2)
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    resp = HttpResponse(buf.getvalue(), content_type='image/png')
    if request.GET.get('download'):
        safe = re.sub(r'[^a-zA-Z0-9]+', '_', event.name).strip('_') or 'event'
        resp['Content-Disposition'] = f'attachment; filename="{safe}_QR.png"'
    return resp


@event_view
def event_poster(request, event):
    return render(request, 'console/poster.html', {'event': event,
                                                   'join_url': f'https://attnd.app/join/{event.event_code}/'})


# ---------------------------------------------------------------------------
# Locations
# ---------------------------------------------------------------------------

@event_view
def locations(request, event):
    from console.usage import net_hearts
    hearts = net_hearts(event)['location']
    locs = list(event.location_set.annotate(n_items=Count('scheduleditem')).order_by('sort_order', 'name'))
    for loc in locs:
        loc.hearts = hearts.get(loc.pk, 0)
    return render(request, 'console/locations.html', _ctx(event, 'locations', locations=locs))


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
    items = event.notification_set.order_by('-send_time')
    devices = Device.objects.real().filter(event=event)
    reach = devices.filter(subscribed=True).exclude(push_token='').count()
    urgent_reach = devices.exclude(push_token='').count()
    live_activities = devices.filter(platform='ios').exclude(live_activity_token='').count()
    return render(request, 'console/notifications.html', _ctx(
        event, 'notifications', form=form, notifications=items, reach=reach, urgent_reach=urgent_reach,
        live_activities=live_activities, urgent_left=push.urgent_sends_left(event)))


@event_view
@require_POST
def notification_action(request, event, notification_id):
    n = get_object_or_404(Notification, pk=notification_id, event=event)
    action = request.POST.get('action')
    if action == 'delete' and not n.sent:
        n.delete()
    elif action == 'send' and not n.sent:
        push.send_notification(n)
        messages.success(request, 'Sent.')
    return redirect('console:notifications', event_id=event.pk)


# ---------------------------------------------------------------------------
# Display / app layout
# ---------------------------------------------------------------------------

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


@event_view
@require_POST
def display_preview(request, event):
    form = DisplayForm(request.POST)
    if not form.is_valid():
        return JsonResponse({'error': form.errors}, status=400)
    original = event.display_config, event.accent_color
    event.display_config = form.to_overrides()
    accent = request.POST.get('accent_color', '')
    event.accent_color = '' if request.POST.get('accent_default') else (
        accent if re.fullmatch(r'#[0-9a-fA-F]{6}', accent or '') else '')
    resolved = resolve_display(event)
    event.display_config, event.accent_color = original
    return JsonResponse({'html': _preview_html(event, resolved), 'resolved': {
        k: v for k, v in resolved.items() if k != 'stats'}})


def _preview_html(event, resolved):
    from scrape.diff import tz_or_utc
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

@event_view
def changesets(request, event):
    sets = event.changesets.exclude(status=ChangeSet.EMPTY)
    return render(request, 'console/changesets.html', _ctx(event, 'changes', changesets=sets,
                                                            jobs=event.ingest_jobs.all()[:10]))


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
            if (settings.TICKETS_ENABLED
                    and cs.changes.filter(entity__in=[Change.ENTITY_TICKET_TYPE, Change.ENTITY_TICKET_SETTINGS]).exists()
                    and not event.can_manage_team(request.user)):
                messages.error(request, 'Only the owner and event admins can apply ticket changes.')
                return redirect('console:changeset', changeset_id=cs.pk)
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
                'longitude': 'Longitude', 'snippet': 'Summary', 'timezone': 'Time zone', 'price_cents': 'Price',
                'quantity': 'Tickets available', 'page_text': 'Ticket page intro',
                'refund_policy_text': 'Refund policy', 'faq': 'FAQ'}


def _fmt(field, value):
    from django.utils.dateparse import parse_datetime
    if settings.TICKETS_ENABLED:
        from tickets.ai import format_value
        if (money := format_value(field, value)) is not None:
            return money
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
    changes = cs.changes.all()
    if not settings.TICKETS_ENABLED:  # ticketing parked: ticket drafts are ignored
        changes = changes.exclude(entity__in=[Change.ENTITY_TICKET_TYPE, Change.ENTITY_TICKET_SETTINGS])
    for ch in changes:
        fields = []
        if ch.op == Change.OP_UPDATE:
            for f, v in ch.data.items():
                old, new = v.get('old'), v.get('new')
                if f == 'location':
                    old, new = v.get('old_name'), v.get('new_name')
                fields.append({'name': f, 'label': FIELD_LABELS.get(f, f.replace('_', ' ').capitalize()),
                               'old': _fmt(f, old), 'new': _fmt(f, new),
                               'conflict': f in ch.conflict_fields, 'is_image': f == 'image_url',
                               'human': f in ch.human_fields})
        elif ch.op == Change.OP_CREATE:
            for f, v in ch.data.items():
                if f in ('location',):
                    continue
                label = 'Location' if f == 'location_name' else FIELD_LABELS.get(f, f.replace('_', ' ').capitalize())
                fields.append({'name': f, 'label': label, 'new': _fmt(f, v), 'is_image': f == 'image_url'})
        rows.append({'change': ch, 'fields': fields})
    return rows


# ---------------------------------------------------------------------------
# History & team
# ---------------------------------------------------------------------------

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

@event_view
def usage(request, event):
    from console.usage import event_usage
    data = event_usage(event, _tz(event))
    audience = Device.objects.real().filter(event=event)
    return render(request, 'console/usage.html', _ctx(event, 'usage', usage=data, in_event_now=audience.count()))


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
