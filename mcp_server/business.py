"""Running the business from chat (owner, Nov 26, 2026): read-only MCP tools over the platform's own models. Every
change still goes through the registered console actions (and their confirmation link when Attnd always asks).

* `whats_new(since)`: new organizers, upgrade / remove-the-limit requests, joins, claims, failed AI jobs.
* `list_organizers(q)`: accounts with their events, and the events without an owner (to reassign).
* `list_support_messages()`: "Talk to a person" messages.
* `list_ai_reviews()`: AI suggestions waiting for review, with the change ids apply_ai_changes takes.
* `list_do_not_contact(email?)`: the one do-not-contact list to check before any send.
* `task_status(task_id)`: a background task (AI import, Studio build) by the id an action returned.
"""

from datetime import timedelta, timezone as dt_timezone

from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime

STAFF = 'platform_admin.view_platformsettings'
ADMIN = 'platform_admin.change_platformsettings'


def _since(value, default_hours=24):
    if isinstance(value, str) and value.strip():
        moment = parse_datetime(value.strip())
        if moment is None:
            day = parse_date(value.strip())
            if day is not None:
                moment = timezone.datetime.combine(day, timezone.datetime.min.time())
        if moment is not None:
            return moment if timezone.is_aware(moment) else timezone.make_aware(moment, dt_timezone.utc)
    return timezone.now() - timedelta(hours=default_hours)


def _iso(dt):
    return dt.isoformat(timespec='minutes') if dt else None


def whats_new(user, since=''):
    from django.contrib.auth import get_user_model
    from django.db.models import Count

    from events.models import Device
    from platform_admin.models import UpgradeRequest
    from prospects.models import ClaimRequest
    from scrape.models import IngestJob
    start = _since(since)
    users = get_user_model().objects.filter(date_joined__gte=start, is_staff=False).annotate(
        n_events=Count('event', distinct=True)).order_by('-date_joined')[:50]
    requests = UpgradeRequest.objects.filter(created_at__gte=start).select_related('event', 'requested_by')[:50]
    joins = (Device.objects.real().attendees().filter(joined_at__gte=start, event__isnull=False)
             .values('event_id', 'event__name').annotate(n=Count('id')).order_by('-n')[:30])
    claims = ClaimRequest.objects.filter(created_at__gte=start).select_related('event')[:50]
    failed = IngestJob.objects.filter(status=IngestJob.FAILED, finished_at__gte=start).select_related('event')[:30]
    return {
        'since': _iso(start),
        'new_organizers': [{'id': u.pk, 'email': u.email, 'joined': _iso(u.date_joined), 'events': u.n_events}
                           for u in users],
        'upgrade_requests': [{'id': r.pk, 'event_id': r.event_id, 'event': r.event.name, 'kind': r.kind,
                              'status': r.status, 'price_usd': str(r.price_usd), 'when': _iso(r.created_at),
                              'by': r.requested_by.email if r.requested_by_id else ''} for r in requests],
        'joins': [{'event_id': j['event_id'], 'event': j['event__name'], 'phones': j['n']} for j in joins],
        'claims': [{'id': c.pk, 'event_id': c.event_id, 'event': c.event.name, 'email': c.email,
                    'status': c.status, 'when': _iso(c.created_at)} for c in claims],
        'failed_ai_jobs': [{'id': str(j.pk), 'event': j.event.name if j.event_id else '', 'kind': j.kind,
                            'error': (j.error or '')[:200], 'when': _iso(j.finished_at)} for j in failed],
    }


def list_organizers(user, q=''):
    from django.contrib.auth import get_user_model
    from django.db.models import Q

    from events.models import Event
    users = get_user_model().objects.filter(event__isnull=False).distinct().order_by('email')
    if q:
        users = users.filter(Q(email__icontains=q) | Q(first_name__icontains=q) | Q(last_name__icontains=q))
    out = []
    for u in users[:100]:
        events = Event.objects.filter(user=u).order_by('-start_time')[:20]
        out.append({'id': u.pk, 'email': u.email, 'name': u.get_full_name(), 'staff': u.is_staff,
                    'last_login': _iso(u.last_login),
                    'events': [{'id': e.pk, 'name': e.name, 'code': e.event_code, 'status': e.status,
                                'plan': e.plan if hasattr(e, 'plan') else '', 'paid': e.paid} for e in events]})
    unassigned = Event.objects.filter(user__isnull=True).order_by('-start_time')[:50]
    return {'organizers': out,
            'unassigned_events': [{'id': e.pk, 'name': e.name, 'code': e.event_code, 'status': e.status,
                                   'showcase': e.showcase} for e in unassigned],
            'note': 'Reassign an event with assign_event_owner (it asks for confirmation).'}


def list_support_messages(user, include_handled=False):
    from agent.models import SupportMessage
    qs = SupportMessage.objects.all() if include_handled else SupportMessage.objects.filter(handled=False)
    return {'messages': [{'id': m.pk, 'from': m.email, 'when': _iso(m.created_at), 'page': m.page,
                          'message': m.message, 'run_id': m.run_id, 'handled': m.handled} for m in qs[:50]]}


def list_ai_reviews(user):
    from events.models import Event
    from scrape.models import ChangeSet
    events = Event.objects.for_user(user, 'events.change_event')
    sets = (ChangeSet.objects.filter(status=ChangeSet.PENDING, event__in=events).select_related('event')
            .prefetch_related('changes').order_by('-created_at')[:20])
    out = []
    for cs in sets:
        changes = [{'id': c.pk, 'entity': c.entity, 'op': c.op, 'label': c.label, 'reason': (c.reason or '')[:200],
                    'conflicts': c.conflict_fields} for c in cs.changes.all()[:80]]
        out.append({'changeset_id': cs.pk, 'event_id': cs.event_id, 'event': cs.event.name,
                    'created': _iso(cs.created_at), 'summary': cs.summary[:500], 'changes': changes})
    return {'reviews': out, 'note': 'Apply with apply_ai_changes (the change ids to accept) or reject with '
                                    'discard_ai_changes; both ask for confirmation.'}


def task_status(user, task_id):
    import uuid

    from console.views_events import task_json
    from events.models import Event
    from scrape.models import IngestJob
    task_id = str(task_id or '').strip()
    if task_id.startswith('studio-') and task_id[7:].isdigit():
        from studio.access import has_studio_access
        from studio.models import StudioRun
        from studio.views import run_task
        run = StudioRun.objects.filter(pk=int(task_id[7:])).select_related('site__event').first()
        if run is None or not has_studio_access(user) or not run.site.event.can_edit(user):
            return {'error': 'No such task.'}
        return {'task': run_task(run)}
    try:
        job = IngestJob.objects.select_related('event').get(pk=uuid.UUID(task_id))
    except (ValueError, IngestJob.DoesNotExist):
        return {'error': 'No such task.'}
    if job.created_by_id != user.pk and not (job.event_id and Event.objects.for_user(user, 'events.change_event')
                                             .filter(pk=job.event_id).exists()):
        return {'error': 'No such task.'}
    return {'task': task_json(job)}


def _obj(props=None, required=()):
    return {'type': 'object', 'additionalProperties': False, 'properties': props or {}, 'required': list(required)}


STR = {'type': 'string'}
TOOLS = [
    ('whats_new', STAFF, 'What changed since a time: new organizers, upgrade and remove-the-limit requests, phones '
     'joined per event, event claims and failed AI jobs (the daily feed).',
     _obj({'since': {'type': 'string', 'description': 'ISO date or date-time (default: the last 24 hours)'}}),
     lambda u, a: whats_new(u, a.get('since') or '')),
    ('list_organizers', ADMIN, 'Organizer accounts with their events (id, code, status, plan), and the events without '
     'an owner (showcase and others) to reassign with assign_event_owner.',
     _obj({'q': {'type': 'string', 'description': 'Search email or name'}}),
     lambda u, a: list_organizers(u, str(a.get('q') or '')[:100])),
    ('list_support_messages', ADMIN, '"Talk to a person" messages from organizers (newest first; unhandled only '
     'unless include_handled).', _obj({'include_handled': {'type': 'boolean'}}),
     lambda u, a: list_support_messages(u, bool(a.get('include_handled')))),
    ('list_ai_reviews', 'signed_in', 'AI suggestions waiting for review on your events: each change set with its '
     'changes (ids for apply_ai_changes).', _obj(), lambda u, a: list_ai_reviews(u)),
    ('list_do_not_contact', 'prospects.view_prospect', 'The do-not-contact list to check before any outreach email: '
     'every blocked address, or with `email` whether that one is blocked.',
     _obj({'email': {'type': 'string', 'description': 'Check this address'}}),
     lambda u, a: __import__('prospects.summary', fromlist=['x']).do_not_contact(u, str(a.get('email') or ''))),
    ('task_status', 'signed_in', 'A background task (AI import, colours, details, Studio site build) by the id an '
     'action returned (Studio builds: "studio-<n>"): status, steps and the result link.',
     _obj({'task_id': STR}, ['task_id']), lambda u, a: task_status(u, a.get('task_id'))),
]


def allowed(user, perm):
    return user.is_authenticated and (perm == 'signed_in' or user.has_perm(perm))


def definitions(user):
    return [{'name': n, 'description': d, 'inputSchema': schema} for n, perm, d, schema, _ in TOOLS
            if allowed(user, perm)]


def run(user, name, args):
    """(data or None when it isn't one of these tools)."""
    for n, perm, _, _, fn in TOOLS:
        if n == name:
            if not allowed(user, perm):
                return {'error': 'Not available to you.'}
            return fn(user, args if isinstance(args, dict) else {})
    return None
