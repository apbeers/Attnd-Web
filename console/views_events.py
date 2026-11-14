"""
Organizer console: create events with AI, edit them by hand, review AI refreshes, configure the app.

Every event page runs with the event's time zone active, so datetime fields display and parse in
the organizer's local time.
"""

import io
import json
import re
from datetime import timedelta
from functools import wraps
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import qrcode
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
from events.models import (Device, Event, EventMember, EventRevision, Location, Notification, ScheduledItem,
                           events_for_user, generate_event_code)
from scrape import diff, jobs
from scrape.models import Change, ChangeSet, IngestJob


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
                        n_devices=Count('device', distinct=True),
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
            job = _job_from_form(ai_form, kind=IngestJob.KIND_CREATE, trigger=IngestJob.TRIGGER_CONSOLE,
                                 created_by=request.user,
                                 timezone=request.POST.get('browser_tz', '')[:64])
            jobs.enqueue(job)
            return redirect('console:job', job_id=job.pk)
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
                                if job.kind == IngestJob.KIND_REFRESH and job.result.get('pending')
                                else reverse('console:event', args=[job.event_id]))
        return JsonResponse(data)
    if job.status == IngestJob.DONE and job.event_id and job.kind == IngestJob.KIND_CREATE:
        if job.result.get('notes'):
            messages.info(request, f"AI notes: {job.result['notes']}")
        return redirect('console:event', event_id=job.event_id)
    return render(request, 'console/job.html', {'job': job, 'event': job.event})


# ---------------------------------------------------------------------------
# Event overview / edit / lifecycle
# ---------------------------------------------------------------------------

@event_view
def event_overview(request, event):
    stats = event_stats(event)
    devices = Device.objects.filter(event=event)
    audience = {
        'joined': devices.count(),
        'subscribed': devices.filter(subscribed=True).exclude(push_token='').count(),
        'active_today': devices.filter(last_seen__gte=timezone.now() - timedelta(hours=24)).count(),
        'ios': devices.filter(platform='ios').count(),
        'android': devices.filter(platform='android').count(),
    }
    sources_form = SourcesForm(instance=event)
    return render(request, 'console/event_overview.html', _ctx(
        event, 'overview', stats=stats, audience=audience, sources_form=sources_form,
        refresh_form=RefreshForm(),
        recent_jobs=event.ingest_jobs.all()[:5],
        changesets=event.changesets.exclude(status=ChangeSet.EMPTY)[:5],
        display=resolve_display(event, stats),
        join_url=f'https://attnd.app/join/{event.event_code}/',
    ))


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
    form = SourcesForm(request.POST, instance=event)
    if form.is_valid():
        form.save()
        messages.success(request, 'Refresh settings saved.')
    else:
        messages.error(request, 'Please fix the source URLs.')
    return redirect('console:event', event_id=event.pk)


@event_view
@require_POST
def event_refresh(request, event):
    form = RefreshForm(request.POST, request.FILES)
    if not form.is_valid():
        messages.error(request, ' '.join(e for errs in form.errors.values() for e in errs))
        return redirect('console:event', event_id=event.pk)
    if not (event.source_urls or form.cleaned_data.get('text') or form.cleaned_data.get('upload')):
        messages.error(request, 'Add a source website first (below), or paste text / upload a file.')
        return redirect('console:event', event_id=event.pk)
    job = _job_from_form(form, kind=IngestJob.KIND_REFRESH, trigger=IngestJob.TRIGGER_CONSOLE,
                         event=event, created_by=request.user, timezone=event.timezone)
    jobs.enqueue(job)
    return redirect('console:job', job_id=job.pk)


@event_view
@require_POST
def event_delete(request, event):
    if event.user_id != request.user.id and not request.user.is_superuser:
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
    locs = event.location_set.annotate(n_items=Count('scheduleditem')).order_by('sort_order', 'name')
    return render(request, 'console/locations.html', _ctx(event, 'locations', locations=locs))


@event_view
def location_edit(request, event, location_id=None):
    loc = get_object_or_404(Location, pk=location_id, event=event) if location_id else Location(event=event)
    categories = sorted(set(event.location_set.exclude(category='').values_list('category', flat=True)))
    form = LocationForm(request.POST or None, request.FILES or None, instance=loc, categories=categories)
    if request.method == 'POST' and form.is_valid():
        loc = form.save(commit=False)
        if not loc.pk:
            loc.user = event.user
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
                                                               items=items, categories=categories))


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
    days = []
    for item in items:
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
            item.user = event.user
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
    return render(request, 'console/item_edit.html', _ctx(event, 'schedule', form=form, item=item))


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
    form = NotificationForm(request.POST or None, initial={'send_time': timezone.now() + timedelta(hours=1)})
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
                messages.success(request, f'Sent to {n.delivered_count} device(s). '
                                          'It also appears in the app’s Updates tab.')
        else:
            n.save()
            messages.success(request, 'Scheduled.')
        return redirect('console:notifications', event_id=event.pk)
    items = event.notification_set.order_by('-send_time')
    reach = Device.objects.filter(event=event, subscribed=True).exclude(push_token='').count()
    return render(request, 'console/notifications.html', _ctx(event, 'notifications', form=form,
                                                               notifications=items, reach=reach))


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
        messages.success(request, 'App layout saved. Attendees get it on their next refresh.')
        return redirect('console:display', event_id=event.pk)
    stats = event_stats(event)
    resolved = resolve_display(event, stats)
    return render(request, 'console/display.html', _ctx(
        event, 'display', form=form, resolved=resolved, stats=stats, labels=LABELS,
        preview=_preview_html(event, resolved)))


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
                'longitude': 'Longitude', 'snippet': 'Summary', 'timezone': 'Time zone'}


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
    for ch in cs.changes.all():
        fields = []
        if ch.op == Change.OP_UPDATE:
            for f, v in ch.data.items():
                old, new = v.get('old'), v.get('new')
                if f == 'location':
                    old, new = v.get('old_name'), v.get('new_name')
                fields.append({'name': f, 'label': FIELD_LABELS.get(f, f.replace('_', ' ').capitalize()),
                               'old': _fmt(f, old), 'new': _fmt(f, new),
                               'conflict': f in ch.conflict_fields, 'is_image': f == 'image_url'})
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
    form = TeamInviteForm(request.POST or None)
    is_owner = event.user_id == request.user.id or request.user.is_superuser
    if request.method == 'POST' and is_owner:
        if request.POST.get('remove'):
            EventMember.objects.filter(event=event, pk=request.POST['remove']).delete()
            messages.info(request, 'Removed.')
            return redirect('console:team', event_id=event.pk)
        if form.is_valid():
            user = User.objects.filter(email__iexact=form.cleaned_data['email']).first()
            if user is None:
                messages.error(request, 'No Attnd account uses that email yet. Ask them to sign up at '
                                        'attnd.app/console/signup/ first, then add them here.')
            elif user.id == event.user_id:
                messages.info(request, 'They already own this event.')
            else:
                EventMember.objects.get_or_create(event=event, user=user)
                messages.success(request, f'{user.email} can now manage this event.')
            return redirect('console:team', event_id=event.pk)
    return render(request, 'console/team.html', _ctx(event, 'team', form=form, is_owner=is_owner,
                                                      members=event.members.select_related('user')))
