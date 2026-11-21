"""Prospects dashboard (staff only): /console/prospects/. Everyone else gets a 404; the console's two-factor
middleware already requires a strong second factor for staff. Page text from the web is untrusted: templates
escape it. Attnd never emails prospects; outreach is done by a person (CAN-SPAM, do-not-contact)."""

import csv
from datetime import timedelta

from django import forms
from django.conf import settings
from django.contrib import messages
from django.db.models import Count, Prefetch, Q
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date

from agent import summaries as S
from agent.registry import agent_action, param as P

from diagnostics.views import staff_required
from prospects import discover, testing
from prospects.models import Prospect, ProspectActivity, ProspectContact, ProspectRun, ProspectSettings

SORTS = {'fit': ('-fit_score', 'start_date', 'id'), 'date': ('start_date', '-fit_score', 'id'),
         'newest': ('-created_at', 'id')}
PAGE = 300


class SettingsForm(forms.ModelForm):
    class Meta:
        model = ProspectSettings
        fields = ['region', 'timezone', 'themes', 'horizon_days', 'max_candidates', 'schedule_enabled',
                  'interval_days']
        widgets = {'region': forms.Textarea(attrs={'rows': 3}), 'themes': forms.Textarea(attrs={'rows': 10})}
        labels = {'horizon_days': 'Look ahead (days)', 'max_candidates': 'New events read per run',
                  'schedule_enabled': 'Run on a schedule', 'interval_days': 'Every (days)'}

    def clean_timezone(self):
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
        value = self.cleaned_data['timezone'].strip()
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            raise forms.ValidationError('Use an IANA time zone such as America/Chicago.')
        return value

    def clean_horizon_days(self):
        return min(max(self.cleaned_data['horizon_days'], 1), 365)

    def clean_max_candidates(self):
        return min(max(self.cleaned_data['max_candidates'], 1), 200)

    def clean_interval_days(self):
        return min(max(self.cleaned_data['interval_days'], 1), 90)


def _filtered(request):
    qs = Prospect.objects.all()
    f = {k: request.GET.get(k, '').strip() for k in ('q', 'status', 'min_fit', 'since', 'until', 'contacts', 'sort')}
    if f['q']:
        q = f['q']
        from attnd.search import ids  # F3: full text + typo-tolerant trigram match
        found = ids(Prospect.objects.all(), q, {'name': 'A', 'organizer': 'A', 'venue': 'B', 'city': 'B',
                                                'notes': 'D'})
        contacts = ids(ProspectContact.objects.all(), q, {'name': 'A', 'organization': 'B'})
        found += list(ProspectContact.objects.filter(pk__in=contacts).values_list('prospect_id', flat=True))
        qs = qs.filter(Q(pk__in=found) | Q(contacts__email__icontains=q)).distinct()
    if f['status'] == 'open':
        qs = qs.exclude(status__in=[Prospect.DISMISSED, Prospect.LOST])
    elif f['status']:
        qs = qs.filter(status=f['status'])
    if f['min_fit'].isdigit():
        qs = qs.filter(fit_score__gte=int(f['min_fit']))
    since, until = parse_date(f['since']), parse_date(f['until'])
    if since:
        qs = qs.filter(Q(end_date__gte=since) | Q(end_date__isnull=True, start_date__gte=since))
    if until:
        qs = qs.filter(start_date__lte=until)
    if f['contacts'] == '1':
        qs = qs.filter(contacts__isnull=False).distinct()
    return qs.order_by(*SORTS.get(f['sort'], SORTS['fit'])), f


@agent_action('create_test_events', 'Import selected prospects as testing-only events (AI import).', confirm=True,
              summary=S.create_test_events, fixed={'action': 'create_test_events'},
              params={'ids': P('array', 'prospect ids', items={'type': 'integer'})}, title='Started test event imports')
@agent_action('set_prospects_status', 'Set the pipeline status of selected prospects.', fixed={'action': 'status'},
              params={'ids': P('array', 'prospect ids', items={'type': 'integer'}),
                      'status': P('string', 'the new status', enum=['new', 'reviewing', 'test_event_created',
                                                                    'contacted', 'interested', 'won', 'lost',
                                                                    'dismissed'])},
              title='Set prospect statuses')
@agent_action('prospects_list', 'The prospects pipeline (filters as ?q=, ?status=, ?sort=).', kind='read')
@staff_required
def prospect_list(request):
    if request.method == 'POST':
        return _bulk(request)
    qs, filters = _filtered(request)
    prospects = list(qs.select_related('event', 'ingest_job')
                     .prefetch_related(Prefetch('contacts', queryset=ProspectContact.objects.order_by('id')))
                     .annotate(contact_count=Count('contacts', distinct=True))[:PAGE])
    counts = dict(Prospect.objects.values_list('status').annotate(n=Count('id')))
    return render(request, 'prospects/list.html', {
        'tab': 'prospects', 'prospects': prospects, 'filters': filters, 'statuses': Prospect.STATUS_CHOICES,
        'status_counts': [(v, label, counts[v]) for v, label in Prospect.STATUS_CHOICES if counts.get(v)],
        'total': sum(counts.values()), 'running': discover.running_run(),
        'export_query': request.GET.urlencode(), 'page_size': PAGE})


def _selected(request):
    ids = [int(i) for i in request.POST.getlist('ids') if i.isdigit()]
    return list(Prospect.objects.filter(pk__in=ids))


def _bulk(request):
    action = request.POST.get('action', '')
    chosen = _selected(request)
    back = reverse('prospects:list') + ('?' + request.POST['back'] if request.POST.get('back') else '')
    if not chosen:
        messages.error(request, 'Select at least one prospect.')
        return redirect(back)
    if action == 'create_test_events':
        tz = ProspectSettings.load().timezone
        started, problems = 0, []
        for prospect in chosen:
            try:
                testing.create_test_event(prospect, request.user, tz)
                started += 1
            except testing.CannotCreate as exc:
                problems.append(str(exc))
        if started:
            messages.success(request, f'{started} test event import(s) started. They show up in Your events '
                                      '(Testing only) when done.')
        for p in problems[:5]:
            messages.warning(request, p)
    elif action in ('status', 'dismiss'):
        status = Prospect.DISMISSED if action == 'dismiss' else request.POST.get('status', '')
        if status not in dict(Prospect.STATUS_CHOICES):
            messages.error(request, 'Choose a status.')
            return redirect(back)
        label = dict(Prospect.STATUS_CHOICES)[status]
        for prospect in chosen:
            if prospect.status != status:
                prospect.status = status
                prospect.save(update_fields=['status', 'updated_at'])
                ProspectActivity.objects.create(prospect=prospect, user=request.user, text=f'Status: {label}')
        messages.success(request, f'{len(chosen)} prospect(s) set to {label}.')
    else:
        messages.error(request, 'Unknown action.')
    return redirect(back)


@agent_action('save_prospect', "Save a prospect's status and notes.", fixed={'action': 'save'},
              params={'status': P('string', 'pipeline status', nullable=True,
                                  enum=['new', 'reviewing', 'test_event_created', 'contacted', 'interested', 'won',
                                        'lost', 'dismissed']),
                      'notes': P('string', 'notes (replaces them)', nullable=True)},
              title='Saved a prospect')
@agent_action('create_test_event', 'Import one prospect as a testing-only event (AI import).', confirm=True,
              summary=S.create_test_event, fixed={'action': 'create_test_event'}, title='Started a test event import')
@agent_action('toggle_prospect_contact_dnc', 'Mark a prospect contact do-not-contact, or contactable again.',
              fixed={'action': 'dnc'}, params={'contact': P('integer', 'the contact id')},
              title='Changed do-not-contact')
@agent_action('delete_prospect_contact', 'Remove a prospect contact.', confirm=True, fixed={'action': 'delete_contact'},
              params={'contact': P('integer', 'the contact id')}, title='Removed a contact')
@agent_action('prospect_page', 'One prospect: details, contacts, activity.', kind='read')
@staff_required
def prospect_detail(request, pk):
    prospect = get_object_or_404(Prospect.objects.select_related('event', 'ingest_job', 'first_seen_run'), pk=pk)
    if request.method == 'POST':
        action = request.POST.get('action', '')
        if action == 'save':
            status = request.POST.get('status', prospect.status)
            if status in dict(Prospect.STATUS_CHOICES) and status != prospect.status:
                prospect.status = status
                ProspectActivity.objects.create(prospect=prospect, user=request.user,
                                                text=f'Status: {prospect.get_status_display()}')
            notes = request.POST.get('notes', '')[:10000]
            if notes != prospect.notes:
                prospect.notes = notes
                ProspectActivity.objects.create(prospect=prospect, user=request.user, text='Notes edited')
            prospect.save()
            messages.success(request, 'Saved.')
        elif action == 'create_test_event':
            try:
                job = testing.create_test_event(prospect, request.user, ProspectSettings.load().timezone)
                messages.success(request, 'Test event import started.')
                return redirect('console:job', job_id=job.pk)
            except testing.CannotCreate as exc:
                messages.error(request, str(exc))
        elif action == 'dnc':
            contact = get_object_or_404(ProspectContact, pk=request.POST.get('contact'), prospect=prospect)
            contact.do_not_contact = not contact.do_not_contact
            contact.save(update_fields=['do_not_contact'])
            ProspectActivity.objects.create(
                prospect=prospect, user=request.user,
                text=f'{contact} marked {"do not contact" if contact.do_not_contact else "contactable"}')
        elif action == 'delete_contact':
            contact = get_object_or_404(ProspectContact, pk=request.POST.get('contact'), prospect=prospect)
            ProspectActivity.objects.create(prospect=prospect, user=request.user, text=f'Contact removed: {contact}')
            contact.delete()
        else:
            raise Http404
        return redirect('prospects:detail', pk=prospect.pk)
    return render(request, 'prospects/detail.html', {
        'tab': 'prospects', 'p': prospect, 'contacts': list(prospect.contacts.all()),
        'activity': list(prospect.activity.select_related('user')[:50]), 'statuses': Prospect.STATUS_CHOICES,
        'active_job': testing.active_job(prospect)})


@agent_action('run_prospect_discovery', 'Start a prospect discovery run now (AI web search; costs up to the daily cap).',
              title='Started prospect discovery')
@agent_action('prospect_runs', 'Prospect discovery runs and today\'s spend.', kind='read')
@staff_required
def runs(request):
    if request.method == 'POST':
        running = discover.running_run()
        if running:
            messages.info(request, f'Run {running.pk} is already going.')
        elif discover.budget_left() <= 0:
            messages.error(request, f"Today's prospect AI budget (${discover._daily_cap():.2f}) is "
                                    'used up. It resets at midnight UTC.')
        else:
            run = discover.start_background(request.user)
            messages.success(request, 'Discovery started. This page updates while it runs.')
            return redirect('prospects:run', pk=run.pk)
        return redirect('prospects:runs')
    return render(request, 'prospects/runs.html', {
        'tab': 'runs', 'runs': ProspectRun.objects.select_related('started_by')[:100],
        'running': discover.running_run(), 'spent_today': discover.spent_today(),
        'cap': discover._daily_cap(), 'conf': ProspectSettings.load()})


@agent_action('prospect_run', 'One discovery run: log and events found.', kind='read')
@staff_required
def run_detail(request, pk):
    run = get_object_or_404(ProspectRun, pk=pk)
    return render(request, 'prospects/run.html', {
        'tab': 'runs', 'run': run, 'found': run.first_seen.order_by('-fit_score')[:200]})


@agent_action('save_prospect_settings', 'Prospect discovery settings (region, themes, schedule).',
              form=SettingsForm, prefill=True, title='Saved prospect settings')
@agent_action('prospect_settings', 'Prospect discovery settings.', kind='read')
@staff_required
def settings_view(request):
    conf = ProspectSettings.load()
    form = SettingsForm(request.POST or None, instance=conf)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Settings saved.')
        return redirect('prospects:settings')
    from prospects.schedule import is_due
    next_run = None
    if conf.schedule_enabled:
        next_run = (conf.last_run_at + timedelta(days=conf.interval_days)) if conf.last_run_at else timezone.now()
    return render(request, 'prospects/settings.html', {
        'tab': 'settings', 'form': form, 'conf': conf, 'next_run': next_run, 'due': is_due(conf),
        'cap': discover._daily_cap(), 'model': settings.PROSPECT_MODEL,
        'max_minutes': settings.PROSPECT_RUN_MAX_MINUTES})


def csv_safe(value):
    """No spreadsheet formulas from web text (CSV injection)."""
    text = '' if value is None else str(value)
    return "'" + text if text[:1] in ('=', '+', '-', '@', '\t', '\r') else text


@staff_required
def export_csv(request):
    qs, _ = _filtered(request)
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="attnd-prospects-{timezone.localdate():%Y-%m-%d}.csv"'
    writer = csv.writer(response)
    writer.writerow(['prospect_id', 'event', 'organizer', 'status', 'fit', 'start', 'end', 'city', 'venue', 'url',
                     'test_event_code', 'contact_name', 'role', 'organization', 'email', 'phone', 'website',
                     'profile', 'found_on', 'do_not_contact'])
    for p in qs.select_related('event').prefetch_related('contacts'):
        base = [p.pk, p.name, p.organizer, p.get_status_display(), p.fit_score, p.start_date or '', p.end_date or '',
                p.city, p.venue, p.primary_url, p.event.event_code if p.event else '']
        contacts = list(p.contacts.all()) or [None]
        for c in contacts:
            # Do not contact: the row stays (so nobody adds the person again) without the ways to reach them.
            dnc = bool(c and c.do_not_contact)
            extra = ([c.name, c.role, c.organization, '' if dnc else c.email, '' if dnc else c.phone, c.website,
                      '' if dnc else c.profile_url, c.source_url, 'yes' if dnc else 'no'] if c else [''] * 9)
            writer.writerow([csv_safe(v) for v in base + extra])
    return response


@agent_action('approve_event_claim', 'Hand a prospect test event to the person who claimed it (owner = them; the '
              'free limit stays until paid).', confirm=True, fixed={'action': 'approve'},
              params={'claim': P('integer', 'The claim id')}, title='Approved a claim')
@agent_action('decline_event_claim', 'Decline a claim of a prospect test event.', fixed={'action': 'decline'},
              params={'claim': P('integer', 'The claim id')}, title='Declined a claim')
@agent_action('prospect_claims', 'Claims of prospect test events from the home page, waiting first.', kind='read')
@staff_required
def claims_view(request):
    from prospects import claims
    from prospects.models import ClaimRequest
    if request.method == 'POST':
        claim = get_object_or_404(ClaimRequest, pk=request.POST.get('claim') or 0)
        if claim.status != ClaimRequest.PENDING:
            messages.info(request, 'That claim was already decided.')
        elif request.POST.get('action') == 'approve':
            claims.approve(claim, request.user)
            messages.success(request, f'{claim.event.name} now belongs to {claim.email}.')
        elif request.POST.get('action') == 'decline':
            claims.decline(claim, request.user)
            messages.info(request, 'Claim declined.')
        return redirect('prospects:claims')
    rows = ClaimRequest.objects.select_related('event', 'prospect', 'user', 'decided_by').order_by(
        '-status', '-created_at')[:200]  # pending sorts first ('pending' > 'declined' > 'approved')
    return render(request, 'prospects/claims.html', {'tab': 'claims', 'claims': rows,
                                                      'waiting': ClaimRequest.objects.filter(status='pending').count()})
