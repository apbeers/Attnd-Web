"""Website watching: keep an event in sync with its own web pages without paying for AI on every check.

How it stays cheap:
1. Every few minutes (Heroku Scheduler, `run_scheduled_tasks`) each watched event whose check is due
   re-fetches its pages: the organizer's source URLs plus the pages the AI actually read from them.
2. Requests are conditional (If-None-Match / If-Modified-Since), so an unchanged page is often a
   bodiless 304. Otherwise the page is reduced to what a reader sees (visible text + JSON-LD event data,
   no scripts, styles or volatile timestamps) and hashed.
3. Only when a hash differs does the AI run, and then only on the changed pages (no crawl), with a
   minimum gap and a daily cap per event. A page's new hash is recorded as read only after the AI
   run succeeds, so a failed run is retried on the next check.

How often a page is checked depends on how close the event is (see `check_interval`).
"""
import hashlib
import logging
import re
from datetime import timedelta

import httpx
from django.conf import settings
from django.utils import timezone

from events.models import Event
from scrape.models import ChangeSet, IngestJob, SourcePage

logger = logging.getLogger(__name__)

MAX_PAGES_PER_EVENT = 25
_VOLATILE = [
    re.compile(r'"date(?:Modified|Published|Created)"\s*:\s*"[^"]*"'),   # JSON-LD bookkeeping dates
    re.compile(r'\b(?:updated|posted|published)\s+\d+\s+\w+\s+ago\b', re.I),  # "updated 5 minutes ago"
    re.compile(r'©\s*\d{4}(?:\s*[-–]\s*\d{4})?'),                         # © 2026
]


def min_ai_gap():
    return timedelta(minutes=getattr(settings, 'WATCH_MIN_AI_GAP_MINUTES', 15))


def max_ai_runs_per_day():
    return getattr(settings, 'WATCH_MAX_AI_RUNS_PER_DAY', 24)


def check_interval(event, now):
    """How often to check this event's pages, or None to stop watching.

    During the event (from 12 h before it starts until 6 h after it ends) every 10 minutes; in the week
    before, hourly; earlier than that, every 6 hours; from a day after it ends, not at all.
    """
    end = event.end_time or (event.start_time + timedelta(days=1))
    if now > end + timedelta(days=1):
        return None
    if event.start_time - timedelta(hours=12) <= now <= end + timedelta(hours=6):
        return timedelta(minutes=10)
    if now >= event.start_time - timedelta(days=7):
        return timedelta(hours=1)
    return timedelta(hours=6)


def fingerprint(response):
    """A hash of what a reader sees on the page, ignoring markup, scripts and volatile bits."""
    content_type = response.headers.get('content-type', '')
    if 'html' in content_type:
        from scrape.service import _page_text
        text = _page_text(response.text)
        for pattern in _VOLATILE:
            text = pattern.sub('', text)
        data = ' '.join(text.split()).encode()
    else:
        data = response.content  # PDFs, images, plain text: the bytes themselves
    return hashlib.sha256(data).hexdigest()


def sync_pages(event, discovered=()):
    """Make sure the organizer's URLs (and any pages the AI read) are watched."""
    wanted = list(dict.fromkeys(list(event.source_urls or []) + list(discovered)))[:MAX_PAGES_PER_EVENT]
    existing = set(event.source_pages.values_list('url', flat=True))
    for url in wanted:
        if url not in existing and len(existing) < MAX_PAGES_PER_EVENT:
            SourcePage.objects.create(event=event, url=url)
            existing.add(url)
    # Pages from a source URL the organizer removed stop being watched when the URL itself goes.
    removed = [p for p in event.source_pages.all()
               if p.url not in wanted and not any(p.url.startswith(_origin(u)) for u in event.source_urls or [])]
    if removed:
        SourcePage.objects.filter(pk__in=[p.pk for p in removed]).delete()


def _origin(url):
    m = re.match(r'(https?://[^/]+)', url or '')
    return m.group(1) if m else url


def forget_fingerprints(event):
    """After a full AI read the event reflects every page as it is now: the next check re-baselines."""
    SourcePage.objects.filter(event=event).update(content_hash='', pending_hash='', etag='', last_modified='')


def check_page(client, page, now):
    """Fetch one page and update its fingerprint. Returns 'unchanged', 'changed', 'baseline' or 'error'."""
    headers = {}
    if page.etag:
        headers['If-None-Match'] = page.etag
    if page.last_modified:
        headers['If-Modified-Since'] = page.last_modified
    page.checked_at = now
    try:
        response = client.get(page.url, headers=headers)
    except httpx.HTTPError as exc:
        page.failures += 1
        page.last_status = f'error: {type(exc).__name__}'[:32]
        page.save(update_fields=['checked_at', 'failures', 'last_status'])
        return 'error'
    page.last_status = str(response.status_code)
    if response.status_code == 304:
        page.failures = 0
        page.save(update_fields=['checked_at', 'failures', 'last_status'])
        return 'changed' if page.has_pending_change else 'unchanged'
    if response.status_code >= 400:
        page.failures += 1
        page.save(update_fields=['checked_at', 'failures', 'last_status'])
        return 'error'
    page.failures = 0
    page.etag = response.headers.get('etag', '')[:256]
    page.last_modified = response.headers.get('last-modified', '')[:64]
    digest = fingerprint(response)
    fields = ['checked_at', 'failures', 'last_status', 'etag', 'last_modified']
    if not page.content_hash:
        page.content_hash = digest  # first look: the event already reflects this page
        page.pending_hash = ''
        page.save(update_fields=fields + ['content_hash', 'pending_hash'])
        return 'baseline'
    if digest != page.content_hash:
        if digest != page.pending_hash:
            page.changed_at = now
            fields.append('changed_at')
        page.pending_hash = digest
        page.save(update_fields=fields + ['pending_hash'])
        return 'changed'
    page.pending_hash = ''
    page.save(update_fields=fields + ['pending_hash'])
    return 'unchanged'


def _may_run_ai(event, now):
    """Rate limits and queueing rules for watch-triggered AI runs. Returns (ok, reason)."""
    if event.ingest_jobs.filter(status__in=[IngestJob.QUEUED, IngestJob.RUNNING]).exists():
        return False, 'an AI update is already running'
    recent = event.ingest_jobs.filter(trigger=IngestJob.TRIGGER_WATCH, created_at__gte=now - timedelta(days=1))
    if recent.filter(created_at__gte=now - min_ai_gap()).exists():
        return False, 'waiting for the minimum gap between AI updates'
    if recent.count() >= max_ai_runs_per_day():
        return False, 'reached the daily limit of AI updates'
    if event.update_mode == Event.UPDATE_REVIEW and event.changesets.filter(status=ChangeSet.PENDING).exists():
        return False, 'an earlier update is waiting for review'
    return True, ''


def check_event(event, now=None, client=None):
    """Check one event's pages; queue an AI update for the changed ones if allowed.

    Returns {'checked': n, 'changed': [urls], 'queued': bool, 'reason': str}.
    """
    from scrape import jobs
    now = now or timezone.now()
    sync_pages(event)
    own_client = client is None
    client = client or httpx.Client(headers={'User-Agent': 'Mozilla/5.0 (compatible; AttndBot/1.0; +https://attnd.app)'},
                                    timeout=10, follow_redirects=True)
    try:
        results = {page.url: check_page(client, page, now) for page in event.source_pages.all()}
    finally:
        if own_client:
            client.close()
    Event.objects.filter(pk=event.pk).update(sources_checked_at=now)
    changed = [url for url, result in results.items() if result == 'changed']
    summary = {'checked': len(results), 'changed': changed, 'queued': False, 'reason': ''}
    if not changed:
        return summary
    ok, reason = _may_run_ai(event, now)
    summary['reason'] = reason
    if ok:
        job = IngestJob.objects.create(kind=IngestJob.KIND_REFRESH, event=event, trigger=IngestJob.TRIGGER_WATCH,
                                       urls=changed, timezone=event.timezone)
        jobs.enqueue(job)
        summary['queued'] = True
        logger.info('Watch: %s changed on event %s; AI update queued', changed, event.pk)
    else:
        logger.info('Watch: %s changed on event %s; not queued (%s)', changed, event.pk, reason)
    return summary


def due_events(now):
    candidates = (Event.objects.filter(refresh_schedule=Event.REFRESH_WATCH, testing_only=False)
                  .exclude(status=Event.STATUS_ARCHIVED))
    for event in candidates:
        if not event.source_urls:
            continue
        interval = check_interval(event, now)
        if interval is None:
            continue
        if event.sources_checked_at is None or now - event.sources_checked_at >= interval - timedelta(minutes=1):
            yield event


def check_watched_events(now=None):
    """Run from the scheduler. Returns (events checked, AI updates queued)."""
    now = now or timezone.now()
    checked = queued = 0
    with httpx.Client(headers={'User-Agent': 'Mozilla/5.0 (compatible; AttndBot/1.0; +https://attnd.app)'},
                      timeout=10, follow_redirects=True) as client:
        for event in due_events(now):
            try:
                result = check_event(event, now, client=client)
            except Exception:
                logger.exception('Watch: checking event %s failed', event.pk)
                continue
            checked += 1
            queued += result['queued']
    return checked, queued


def mark_read(event, urls):
    """The AI has read these pages: their pending fingerprints become current."""
    for page in SourcePage.objects.filter(event=event, url__in=urls):
        if page.pending_hash:
            page.content_hash = page.pending_hash
            page.pending_hash = ''
            page.save(update_fields=['content_hash', 'pending_hash'])
