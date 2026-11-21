"""
Running AI import / refresh jobs.

Jobs are rows in IngestJob. They are picked up either by
  * a background thread in the web dyno (INGEST_RUN_INLINE, the default — no extra dyno needed), or
  * ``python manage.py process_ingest_jobs`` running as a Heroku worker dyno.
``python manage.py run_scheduled_tasks`` (Heroku Scheduler, every 10 minutes) re-queues jobs that were
interrupted by a dyno restart and starts scheduled refreshes.
"""

import logging
import threading
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.models import User
from django.db import close_old_connections, connection, transaction
from django.utils import timezone

from events.models import Event
from scrape import diff
from scrape.models import Change, ChangeSet, IngestJob
from scrape.service import scrape_event

logger = logging.getLogger(__name__)

TRIAL_USERNAME = 'trialuser'
STALE_AFTER = timedelta(minutes=15)


def trial_user():
    user, created = User.objects.get_or_create(username=TRIAL_USERNAME, defaults={'email': 'trial@attnd.app'})
    if created:
        user.set_unusable_password()
        user.save()
    return user


# ---------------------------------------------------------------------------
# Queueing
# ---------------------------------------------------------------------------

def enqueue(job):
    """Start the job after the current transaction commits."""
    if getattr(settings, 'INGEST_RUN_INLINE', True):
        transaction.on_commit(lambda: _start_background(job.pk))
    return job


def _start_background(job_id):
    threading.Thread(target=_thread_main, args=(job_id,), daemon=True, name=f'ingest-{job_id}').start()


def _thread_main(job_id):
    close_old_connections()
    try:
        run_job(job_id)
    finally:
        connection.close()


def claim(job_id):
    """Atomically move a queued job to running. Returns the job, or None if someone else took it."""
    with transaction.atomic():
        job = IngestJob.objects.select_for_update().filter(pk=job_id, status=IngestJob.QUEUED).first()
        if job is None:
            return None
        job.status = IngestJob.RUNNING
        job.started_at = timezone.now()
        job.progress = 'Starting…'
        job.save(update_fields=['status', 'started_at', 'progress'])
        return job


def claim_next():
    with transaction.atomic():
        qs = IngestJob.objects.select_for_update(skip_locked=True).filter(status=IngestJob.QUEUED).order_by('created_at')
        job = qs.first()
        if job is None:
            return None
        job.status = IngestJob.RUNNING
        job.started_at = timezone.now()
        job.progress = 'Starting…'
        job.save(update_fields=['status', 'started_at', 'progress'])
        return job


def recover_stale():
    """Re-queue jobs left 'running' by a restarted dyno (once), fail them the second time."""
    cutoff = timezone.now() - STALE_AFTER
    count = 0
    for job in IngestJob.objects.filter(status=IngestJob.RUNNING, started_at__lt=cutoff):
        retried = job.result.get('retried', False)
        if retried:
            _fail(job, 'The job was interrupted. Please try again.')
        else:
            job.status = IngestJob.QUEUED
            job.result = {**job.result, 'retried': True}
            job.save(update_fields=['status', 'result'])
            enqueue(job)
        count += 1
    return count


# ---------------------------------------------------------------------------
# Running
# ---------------------------------------------------------------------------

class Steps:
    """Friendly steps for the task tray (`IngestJob.steps`): "Reading 3 web pages", "Found 12 locations and 34
    schedule items", "Waiting for your review". Calling it starts a step (the previous one is done, shown with its
    `done` text); `update` changes the running step's text. `job.progress` keeps the running step's text."""

    def __init__(self, job):
        self.job = job

    def _save(self):
        running = next((s['text'] for s in reversed(self.job.steps) if s['state'] == 'running'), '')
        self.job.progress = running[:256]
        IngestJob.objects.filter(pk=self.job.pk).update(steps=self.job.steps, progress=self.job.progress)

    def _close(self, state='done'):
        for step in self.job.steps:
            if step['state'] == 'running':
                step['state'] = state
                if state == 'done' and step.get('done'):
                    step['text'] = step['done']
                step.pop('done', None)

    def __call__(self, text, done=None):
        self._close()
        self.job.steps = [*self.job.steps, {'text': text[:200], 'state': 'running', 'done': (done or '')[:200],
                                            'at': timezone.now().isoformat()}][-20:]
        self._save()

    def update(self, text, done=None):
        for step in reversed(self.job.steps):
            if step['state'] == 'running':
                step['text'] = text[:200]
                if done:
                    step['done'] = done[:200]
                break
        else:
            return self(text, done)
        self._save()

    def finish(self, text='', state='done'):
        """Close the running step and add a last one (done, or `waiting` for the person)."""
        self._close()
        if text:
            self.job.steps = [*self.job.steps, {'text': text[:200], 'state': state, 'at': timezone.now().isoformat()}]
        self.job.progress = ''

    def failed(self):
        self._close('failed')


def _progress(job, text, done=None):
    Steps(job)(text, done)


def _fail(job, message):
    Steps(job).failed()
    job.status = IngestJob.FAILED
    job.error = message[:2000]
    job.progress = ''
    job.finished_at = timezone.now()
    job.upload_data = None
    job.save()


def run_job(job_id, already_claimed=False):
    job = IngestJob.objects.get(pk=job_id) if already_claimed else claim(job_id)
    if job is None:
        return None
    try:
        if job.kind == IngestJob.KIND_CREATE:
            _run_create(job)
        elif job.kind == IngestJob.KIND_REFRESH:
            _run_refresh(job)
        else:  # e.g. an old 'tickets' draft job (ticketing is parked on git branch parked/tickets)
            raise ValueError('This kind of job is no longer supported.')
    except ValueError as exc:
        logger.warning('Ingest job %s failed: %s', job.pk, exc)
        _fail(job, str(exc))
    except Exception as exc:
        logger.exception('Ingest job %s crashed', job.pk)
        _fail(job, f'Something went wrong while reading your event: {exc}')
    return IngestJob.objects.get(pk=job.pk)


def _attachments(job):
    if job.upload_data:
        return [{'name': job.upload_name, 'mime': job.upload_type, 'data': bytes(job.upload_data)}]
    return []


def _plural(n, word):
    return f'{n} {word}{"" if n == 1 else "s"}'


def _run_create(job):
    steps = Steps(job)
    data = scrape_event(
        urls=job.urls, text=job.text, attachments=_attachments(job), instructions=job.instructions,
        google_api_key=getattr(settings, 'GOOGLE_PLACES_API_KEY', ''), progress=steps,
    )
    n_loc, n_items = len(data.get('locations') or []), len(data.get('schedule') or [])
    steps(f'Saving {_plural(n_loc, "location")} and {_plural(n_items, "schedule item")}…',
          done=f'Found {_plural(n_loc, "location")} and {_plural(n_items, "schedule item")}')
    is_trial = job.created_by is None
    owner = trial_user() if is_trial else job.created_by
    event, notes = diff.create_event_from_extraction(
        data, owner,
        # Trial events are joinable by code/QR straight away but stay out of nearby search until claimed; so are
        # staff test copies of prospects' events (testing_only).
        status=Event.STATUS_PUBLISHED if is_trial or job.testing_only else Event.STATUS_DRAFT,
        public=False,
        fallback_tz=job.timezone or 'UTC',
        source_urls=job.urls,
        source_text=job.text,
    )
    if job.testing_only:
        # Unlisted Demo copy for staff testing: never in nearby search, never website-watched, no Studio.
        Event.objects.filter(pk=event.pk).update(testing_only=True, public=False, paid=False,
                                                 refresh_schedule=Event.REFRESH_OFF)
        event.refresh_from_db()
    from scrape import watch
    watch.sync_pages(event, discovered=data.get('source_pages', []))
    job.event = event
    steps.finish(f'Created “{event.name[:80]}” as a draft: check it, then publish' if event.status == Event.STATUS_DRAFT
                 else f'Created “{event.name[:80]}”')
    job.status = IngestJob.DONE
    job.progress = ''
    job.finished_at = timezone.now()
    job.upload_data = None
    job.result = {
        'event_id': event.id,
        'event_code': event.event_code,
        'event_name': event.name,
        'location_count': event.location_set.count(),
        'item_count': event.scheduleditem_set.count(),
        'notes': ' '.join(filter(None, [data.get('notes', '')] + notes)),
    }
    job.save()
    if job.created_by_id and job.trigger == IngestJob.TRIGGER_CONSOLE and not job.testing_only:
        from console import organizer_mail  # stage I16: "your event is ready" (each person can turn it off)
        organizer_mail.import_done(job, event)
    if job.studio_import and job.urls and job.created_by_id and not job.testing_only:
        _bring_into_studio(job, event)
    if job.testing_only:
        try:
            from prospects.testing import job_finished
            job_finished(job)
        except Exception:
            logger.exception('Could not link test event %s to its prospect', event.pk)


def _bring_into_studio(job, event):
    """Attnd Studio (admins only): an import that creates an event brings its website into Studio automatically (the
    organizer can untick it on the import form): the whole site is crawled, its files copied and the AI rebuilds it.
    Only for staff and superusers (checked again here, when the job runs)."""
    from studio.access import is_staff
    if not is_staff(job.created_by) or event.testing_only:
        return
    try:
        from studio import importer, publishing
        publishing.switch_plan(event, Event.PLAN_STUDIO)
        site = publishing.ensure_site(event, job.created_by)
        importer.start_import(site, job.urls[0], job.created_by)
        job.result = {**job.result, 'studio_import': True}
        job.save(update_fields=['result'])
    except Exception:
        logger.exception('Could not bring %s into Studio', job.urls[0])


def _run_refresh(job):
    event = job.event
    pasted = job.trigger == IngestJob.TRIGGER_PASTE  # only what was pasted or dropped (stage I3)
    urls = job.urls if pasted else (job.urls or event.source_urls)
    text = job.text or ''
    if not urls and not text and not job.upload_data:
        raise ValueError('This event has no sources to refresh from. Add a website or upload a flyer.')
    watched = job.trigger == IngestJob.TRIGGER_WATCH
    steps = Steps(job)
    if watched:
        steps(f'Reading the {_plural(len(urls), "page")} that changed…', done=f'Read the {_plural(len(urls), "page")} that changed')
    data = scrape_event(
        urls=urls, text=text, attachments=_attachments(job), instructions=job.instructions,
        current=diff.current_state(event), google_api_key='',  # geocode only what changes, below
        crawl=not (watched or pasted),  # the watcher re-reads only the pages that changed
        progress=None if watched else steps,
    )
    steps('Comparing with your event…', done='Compared with your event')
    changes = diff.compute_changes(event, data)
    if pasted:  # a flyer or a sheet rarely lists everything: never suggest removing what it doesn't mention
        changes = [c for c in changes if c.get('op') != Change.OP_DELETE]
    if any(c['entity'] == 'location' and c['op'] == 'create' for c in changes) and getattr(settings, 'GOOGLE_PLACES_API_KEY', ''):
        steps('Checking addresses on the map…', done='Checked addresses on the map')
    _geocode_new_locations(event, changes)
    cs = diff.save_changeset(event, job, changes, notes=data.get('notes', ''))

    auto_applied = 0
    if event.update_mode != Event.UPDATE_REVIEW and cs.status == ChangeSet.PENDING and not pasted:
        decisions = diff.default_decisions(cs, minor_only=event.update_mode == Event.UPDATE_MINOR)
        # Even in automatic mode, removals wait for a person (default_decisions already leaves out
        # fields someone edited by hand).
        decisions = {cid: d for cid, d in decisions.items()
                     if cs.changes.filter(pk=cid).exclude(op=Change.OP_DELETE).exists()}
        if decisions:
            auto_applied = diff.apply_changeset(cs, decisions, user=None, partial_ok=True)

    from scrape import watch
    if watched:
        watch.mark_read(event, urls)
    elif urls:
        watch.sync_pages(event, discovered=data.get('source_pages', []))
        watch.forget_fingerprints(event)

    Event.objects.filter(pk=event.pk).update(last_refreshed_at=timezone.now())
    pending = cs.changes.filter(status='pending').count()
    if auto_applied:
        steps(f'Applying {_plural(auto_applied, "change")}…', done=f'Applied {_plural(auto_applied, "change")} automatically')
    if pending:
        steps.finish(f'Found {_plural(pending, "change")}: waiting for your review', state='waiting')
    else:
        steps.finish('Everything is up to date' if not auto_applied else '')
    job.status = IngestJob.DONE
    job.progress = ''
    job.finished_at = timezone.now()
    job.upload_data = None
    job.result = {'changeset_id': cs.id, 'summary': cs.summary, 'pending': pending, 'auto_applied': auto_applied}
    job.save()
    if pending and job.trigger in (IngestJob.TRIGGER_SCHEDULE, IngestJob.TRIGGER_WATCH):
        _email_owner(event, cs, pending)


def _geocode_new_locations(event, changes):
    """Fill coordinates for newly proposed locations, biased to the event's position."""
    key = getattr(settings, 'GOOGLE_PLACES_API_KEY', '')
    if not key:
        return
    from scrape.service import _enrich_with_places

    new_locs = [c['data'] for c in changes if c['entity'] == 'location' and c['op'] == 'create']
    if not new_locs:
        return
    payload = {'name': event.name, 'address': event.address,
               'latitude': float(event.latitude) if event.latitude is not None else None,
               'longitude': float(event.longitude) if event.longitude is not None else None,
               'locations': [{**loc, 'latitude': loc.get('latitude'), 'longitude': loc.get('longitude'),
                              'address': loc.get('address', ''), 'image_url': loc.get('image_url')}
                             for loc in new_locs]}
    enriched = _enrich_with_places(payload, key)
    for target, result in zip(new_locs, enriched['locations']):
        for f in ('latitude', 'longitude', 'address', 'image_url'):
            if result.get(f) and not target.get(f):
                target[f] = result[f]


def _email_owner(event, changeset, pending):
    """Tell the owner and event admins that AI updates are waiting for review (each can turn it off, stage I16)."""
    from console import organizer_mail
    organizer_mail.review_waiting(event, changeset, pending)


