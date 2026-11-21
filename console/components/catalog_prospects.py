"""Prospects as console components (staff only, A19): drawn once for the Prospects pages' rows and the full-screen
canvas / chat. Access is unchanged: `audience='staff'` keeps every one of them (and their tools) away from everyone
else (Http404, absent from the show tool and the canvas). Contact rules are kept: every contact shows its source
page, do-not-contact contacts never show an email or phone here, Attnd never emails prospects.
"""

from django.http import Http404
from django.urls import reverse
from django.utils import timezone

from console.components import component, form_json
from console.components.catalog import fmt

PK = {'pk': 'prospect_id'}


def _staff(user):
    if not (getattr(user, 'is_staff', False) or getattr(user, 'is_superuser', False)):
        raise Http404


def _expand_prospects(user, **_):
    from prospects.models import Prospect
    _staff(user)
    qs = (Prospect.objects.exclude(status__in=[Prospect.DISMISSED, Prospect.LOST])
          .order_by('-fit_score', 'start_date', 'id').values_list('pk', flat=True)[:40])
    return [{'prospect_id': pk} for pk in qs]


def _dates(p):
    if not p.start_date:
        return ''
    if p.end_date and p.end_date != p.start_date:
        return f'{p.start_date:%b %-d} – {p.end_date:%b %-d, %Y}'
    return f'{p.start_date:%b %-d, %Y}'


@component('prospect_row', 'Prospect', 'A prospect (an upcoming event that suits the app, staff only): name, dates, '
           'city, fit score, pipeline status, contacts; Create test event (asks to confirm). With no ids: the open '
           'prospects by fit.', ids=('prospect_id',), expand=_expand_prospects, detail='prospect_detail',
           audience='staff', kwarg_map=PK, actions=(('create_test_event', 'Create test event'),),
           page=lambda ids: reverse('prospects:detail', args=[ids['prospect_id']]))
def prospect_row(user, prospect_id=None, prospect=None):
    from prospects.models import Prospect
    _staff(user)
    p = prospect or Prospect.objects.filter(pk=prospect_id).select_related('event').first()
    if p is None:
        raise Http404
    actions = [] if p.event_id else [{'name': 'create_test_event', 'style': 'outline'}]
    return {'id': p.pk, 'name': p.name, 'organizer': p.organizer, 'dates': _dates(p), 'city': p.city,
            'venue': p.venue, 'fit': p.fit_score, 'status': p.get_status_display(), 'status_key': p.status,
            'contacts': p.contacts.count(), 'event_name': p.event.name if p.event_id else '',
            'url': reverse('prospects:detail', args=[p.pk]), 'actions': actions}


@component('prospect_detail', 'Prospect detail', 'One prospect (staff only): summary, why it fits, links, contacts '
           '(each with the page it came from; do-not-contact honoured), activity, status and notes, Create test event.',
           ids=('prospect_id',), audience='staff', kwarg_map=PK,
           actions=(('save_prospect', 'Save'), ('create_test_event', 'Create test event'),
                    ('toggle_prospect_contact_dnc', 'Do not contact'), ('delete_prospect_contact', 'Remove')),
           page=lambda ids: reverse('prospects:detail', args=[ids['prospect_id']]))
def prospect_detail(user, prospect_id=None, prospect=None):
    from console.components import get, resolve_actions
    from prospects.models import Prospect
    _staff(user)
    p = prospect or Prospect.objects.filter(pk=prospect_id).select_related('event').first()
    if p is None:
        raise Http404
    ids = {'prospect_id': p.pk}
    contacts = []
    for c in p.contacts.order_by('id'):
        row = {'id': c.pk, 'name': c.name or c.organization or 'Contact', 'role': c.role, 'organization': c.organization,
               'email': '' if c.do_not_contact else c.email, 'phone': '' if c.do_not_contact else c.phone,
               'source': c.source_url, 'dnc': c.do_not_contact,
               'actions': [{'name': 'toggle_prospect_contact_dnc', 'style': 'outline',
                            'label': 'Contactable again' if c.do_not_contact else 'Do not contact',
                            'fields': {'contact': c.pk}},
                           {'name': 'delete_prospect_contact', 'style': 'danger', 'fields': {'contact': c.pk},
                            'confirm_text': 'Remove this contact?'}]}
        row['actions'] = [dict(a, **{k: v for k, v in r.items() if k != 'name'}) for a, r in
                          zip(row['actions'], resolve_actions(get('prospect_detail'), ids, row['actions']))]
        contacts.append(row)
    actions = [a for c in contacts for a in c['actions']]
    if not p.event_id:
        actions.append({'name': 'create_test_event', 'style': 'primary'})
    with timezone.override(timezone.get_current_timezone()):
        activity = [{'when': fmt(a.created_at, 'M j, g:i A'), 'text': a.text, 'who': a.user.email if a.user_id else 'Attnd'}
                    for a in p.activity.select_related('user')[:12]]
    return {
        'id': p.pk, 'name': p.name, 'organizer': p.organizer, 'summary': p.summary, 'dates': _dates(p), 'city': p.city,
        'venue': p.venue, 'fit': p.fit_score, 'reasons': list(p.fit_reasons or [])[:6], 'traits': list(p.traits or [])[:8],
        'links': list(p.urls or [])[:5], 'status': p.status, 'statuses': [{'value': v, 'label': lbl} for v, lbl in
                                                                          Prospect.STATUS_CHOICES],
        'notes': p.notes, 'event_name': p.event.name if p.event_id else '',
        'event_url': reverse('console:event', args=[p.event_id]) if p.event_id else '',
        'save_url': reverse('prospects:detail', args=[p.pk]), 'contacts': contacts, 'activity': activity,
        'actions': actions,
    }


def _expand_runs(user, **_):
    from prospects.models import ProspectRun
    _staff(user)
    return [{'prospect_run_id': pk} for pk in ProspectRun.objects.order_by('-created_at').values_list('pk', flat=True)[:10]]


@component('prospect_run_row', 'Discovery run', 'A prospect discovery run (staff only): when, status, found / new / '
           'updated, cost. With no ids: the latest runs.', ids=('prospect_run_id',), expand=_expand_runs,
           audience='staff', page=lambda ids: reverse('prospects:run', args=[ids['prospect_run_id']]))
def prospect_run_row(user, prospect_run_id=None):
    from prospects.models import ProspectRun
    _staff(user)
    r = ProspectRun.objects.filter(pk=prospect_run_id).first()
    if r is None:
        raise Http404
    return {'id': r.pk, 'name': f'Run {r.pk}', 'when': fmt(r.created_at, 'M j, g:i A'), 'status': r.get_status_display(),
            'running': r.status in (ProspectRun.QUEUED, ProspectRun.RUNNING), 'found': r.found, 'new': r.new,
            'updated': r.updated, 'cost': f'{r.cost_usd:.2f}', 'error': r.error[:200],
            'url': reverse('prospects:run', args=[r.pk])}


@component('prospect_settings_form', 'Prospect settings', 'Prospect discovery settings (staff only): region, time '
           'zone, themes, how far ahead, events per run, the schedule; Run now.', ids=(), audience='staff',
           actions=(('run_prospect_discovery', 'Run now'),), page=lambda ids: reverse('prospects:settings'))
def prospect_settings_form(user, form=None):
    from django.conf import settings

    from prospects import discover
    from prospects.models import ProspectSettings
    from prospects.views import SettingsForm
    _staff(user)
    conf = ProspectSettings.load()
    form = form or SettingsForm(instance=conf)
    data = form_json(form, ['region', ('grid', 'timezone', 'horizon_days'), 'themes', 'max_candidates',
                            ('grid', 'schedule_enabled', 'interval_days')],
                     action=reverse('prospects:settings'), submit=[{'label': 'Save', 'style': 'primary'}])
    return {'name': 'Prospect settings', 'form': data, 'running': bool(discover.running_run()),
            'spent': f'{discover.spent_today():.2f}', 'cap': f'{settings.PROSPECT_DAILY_SPEND_CAP_USD:.2f}',
            'actions': [] if discover.running_run() else [{'name': 'run_prospect_discovery', 'style': 'primary'}]}
