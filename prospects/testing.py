"""Testing-only events made from prospects (staff): the existing AI import (scrape.jobs, an IngestJob) reads the
prospect's pages and creates the event, owned by the staff member who asked, published but unlisted
(Event.testing_only: never in nearby search, never website-watched, never brought into Studio, a Demo event).
Nobody else is invited or emailed: the prospect's organizer only hears about Attnd from a person."""

from django.db import transaction

from prospects.models import Prospect, ProspectActivity
from scrape import jobs
from scrape.models import IngestJob

INSTRUCTIONS = ('This is an internal test copy for Attnd staff. Import the event as it is published; '
                'do not add contact details of people.')


class CannotCreate(ValueError):
    pass


def active_job(prospect):
    job = prospect.ingest_job
    return job if job and job.status in (IngestJob.QUEUED, IngestJob.RUNNING) else None


def create_test_event(prospect, user, timezone_name='America/Chicago'):
    """Queue the AI import for one prospect. Returns the IngestJob; raises CannotCreate with the reason."""
    if prospect.event_id:
        raise CannotCreate(f'{prospect.name} already has a test event.')
    if active_job(prospect):
        raise CannotCreate(f'{prospect.name} is already being imported.')
    urls = [u for u in prospect.urls if isinstance(u, str) and u.startswith(('http://', 'https://'))][:3]
    if not urls:
        raise CannotCreate(f'{prospect.name} has no web address to import from.')
    with transaction.atomic():
        job = IngestJob.objects.create(kind=IngestJob.KIND_CREATE, trigger=IngestJob.TRIGGER_PROSPECT,
                                       created_by=user, urls=urls, timezone=timezone_name, instructions=INSTRUCTIONS,
                                       testing_only=True, studio_import=False)
        prospect.ingest_job = job
        prospect.save(update_fields=['ingest_job', 'updated_at'])
        ProspectActivity.objects.create(prospect=prospect, user=user, text='Test event import started')
        jobs.enqueue(job)
    return job


def job_finished(job):
    """Called by scrape.jobs when a testing-only import is done: link the event, move the status on."""
    for prospect in Prospect.objects.filter(ingest_job=job):
        prospect.event = job.event
        if prospect.status in (Prospect.NEW, Prospect.REVIEWING):
            prospect.status = Prospect.TEST_EVENT
        prospect.save(update_fields=['event', 'status', 'updated_at'])
        ProspectActivity.objects.create(prospect=prospect, user=job.created_by,
                                        text=f'Test event created: {job.event.name} ({job.event.event_code})')
