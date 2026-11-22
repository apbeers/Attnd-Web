"""The Platform admin (D2, superusers only; `/console/platform/`): pricing, customers, upgrade requests, event
overrides, AI spend and a health glance. Every change always asks (page `data-confirm`, Ask Attnd confirm=True) and
is a History LogEntry (credit.history)."""
import csv
from functools import wraps

from attnd.permissions import requires
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from agent.registry import agent_action, param as P
from credit import history
from platform_admin import data, plans
from platform_admin.forms import OverrideForm, PricingForm
from platform_admin.models import EventOverride, PaymentRecord, PlatformSettings, UpgradeRequest

TABS = [('overview', 'Overview'), ('pricing', 'Pricing'), ('customers', 'Customers'), ('requests', 'Upgrade requests'),
        ('events', 'Event overrides'), ('ai', 'AI spend')]
PRICING_FIELDS = ['standard_price_usd', 'studio_price_usd', 'free_device_limit']


# The Platform admin: the "Platform admins" role (attnd.permissions) and superusers; 404 for everyone else.
superuser_required = requires('platform_admin.change_platformsettings')


def _back(tab, **extra):
    from urllib.parse import urlencode
    return redirect(reverse('platform:home') + '?' + urlencode({'tab': tab, **extra}))


def _pricing_form(post=None):
    from credit.ledger import limits
    conf = PlatformSettings.load()
    return PricingForm(post, instance=conf, initial={'new_account_daily_imports': limits().new_account_daily_imports})


@agent_action('platform_admin', 'The platform admin (superusers): prices, customers, upgrade requests, event '
              'overrides, AI spend and the health glance. ?tab=overview|pricing|customers|requests|events|ai, '
              '?q= searches customers or events.', kind='read')
@superuser_required
def home(request):
    from events.models import Event
    tab = request.GET.get('tab') if request.GET.get('tab') in dict(TABS) else 'overview'
    q = (request.GET.get('q') or '').strip()
    ctx = {'tabs': TABS, 'tab': tab, 'q': q}
    if tab == 'pricing':
        ctx['form'] = _pricing_form()
    elif tab == 'customers':
        ctx['rows'] = data.customer_rows(data.customers(q, request.GET.get('sort') or '-date_joined', 300))
        ctx['sort'] = request.GET.get('sort') or '-date_joined'
    elif tab == 'requests':
        ctx['pending'] = UpgradeRequest.objects.filter(status=UpgradeRequest.PENDING).select_related('event', 'requested_by')
        ctx['decided'] = (UpgradeRequest.objects.exclude(status=UpgradeRequest.PENDING)
                          .select_related('event', 'requested_by', 'decided_by')[:30])
    elif tab == 'events':
        events = Event.objects.select_related('user').order_by('-created_at')
        if q:
            events = events.filter(name__icontains=q) | events.filter(event_code__iexact=q)
        ctx['events'] = [{'event': e, 'plan': plans.LABELS[plans.plan_of(e)], 'override': EventOverride.of(e)}
                         for e in events[:40]]
    return render(request, 'platform_admin/home.html', ctx)


@agent_action('save_platform_pricing', 'Change the plan prices (Standard, Studio), the free phone limit and AI imports '
              'a day for new accounts (superusers).', confirm=True, form=PricingForm, prefill=True,
              title='Changed the platform prices')
@superuser_required
def save_pricing(request):
    from credit.models import AiLimits
    if request.method != 'POST':  # the form on its own (Ask Attnd's prefill reads it here)
        return render(request, 'platform_admin/home.html', {'tabs': TABS, 'tab': 'pricing', 'form': _pricing_form()})
    conf = PlatformSettings.load()
    if conf._state.adding:
        conf.save()
    before = history.snapshot(conf, PRICING_FIELDS)
    form = _pricing_form(request.POST)
    if not form.is_valid():
        messages.error(request, 'Check the prices: ' + '; '.join(f'{k}: {v[0]}' for k, v in form.errors.items()))
        return _back('pricing')
    conf = form.save(commit=False)
    conf.updated_by = request.user
    conf.save()
    history.log(request.user, conf, before, history.snapshot(conf, PRICING_FIELDS))
    limits = AiLimits.load()
    if limits._state.adding:
        limits.save()
    if limits.new_account_daily_imports != form.cleaned_data['new_account_daily_imports']:
        old = history.snapshot(limits, ['new_account_daily_imports'])
        limits.new_account_daily_imports = form.cleaned_data['new_account_daily_imports']
        limits.updated_by = request.user
        limits.save()
        history.log(request.user, limits, old, history.snapshot(limits, ['new_account_daily_imports']))
    messages.success(request, 'Saved the prices and limits.')
    return _back('pricing')


@agent_action('approve_upgrade_request', 'Approve an upgrade request (superusers): sets the plan and paid, records the '
              'payment at its price.', confirm=True, params={'request_id': P('integer', 'The upgrade request id')},
              title='Approved an upgrade')
@superuser_required
@require_POST
def approve_request(request):
    req = get_object_or_404(UpgradeRequest, pk=request.POST.get('request_id'), status=UpgradeRequest.PENDING)
    event = req.event
    before = history.snapshot(event, ['paid', 'plan'])
    plans.approve(req, request.user)
    history.log(request.user, event, before, history.snapshot(event, ['paid', 'plan']))
    messages.success(request, f'{event.name} is now {plans.LABELS[req.kind]}.')
    return _back('requests')


@agent_action('decline_upgrade_request', 'Decline an upgrade request (superusers).', confirm=True,
              params={'request_id': P('integer', 'The upgrade request id')}, title='Declined an upgrade')
@superuser_required
@require_POST
def decline_request(request):
    req = get_object_or_404(UpgradeRequest, pk=request.POST.get('request_id'), status=UpgradeRequest.PENDING)
    plans.decline(req, request.user)
    history.log(request.user, req, {'status': 'pending'}, {'status': 'declined'})
    messages.success(request, f'Declined the request for {req.event.name}.')
    return _back('requests')


@agent_action('event_admin_overrides', "One event's plan and overrides (superusers).", kind='read')
@superuser_required
def event_detail(request, event_id):
    from events.models import Event
    event = get_object_or_404(Event.objects.select_related('user'), pk=event_id)
    override = EventOverride.of(event) or EventOverride(event=event)
    form = _override_form(event, override)
    return render(request, 'platform_admin/event.html', {
        'tabs': TABS, 'tab': 'events', 'event': event, 'form': form, 'plan': plans.LABELS[plans.plan_of(event)],
        'payments': PaymentRecord.objects.filter(event=event), 'requests': event.upgrade_requests.all()})


def _override_form(event, override, post=None):
    from credit.models import AiLimitOverride
    owner = AiLimitOverride.objects.filter(user=event.user).first() if event.user_id else None
    return OverrideForm(post, instance=override, initial={
        'plan': plans.plan_of(event), 'listed': event.public, 'watching': event.refresh_schedule == 'watch',
        'owner_ai_monthly_usd': owner.monthly_usd if owner else None, 'owner_ai_unlimited': bool(owner and owner.unlimited)})


@agent_action('save_event_overrides', "Set one event's plan (free / standard / studio), free phone limit, website "
              "publishing, Studio AI cap, nearby listing, website watching, its owner's AI credit and notes "
              '(superusers).', confirm=True, form=OverrideForm, prefill='platform:event', title='Changed an event\'s overrides')
@superuser_required
@require_POST
def save_event(request, event_id):
    from credit.models import AiLimitOverride
    from events.models import Event
    event = get_object_or_404(Event, pk=event_id)
    override = EventOverride.of(event) or EventOverride(event=event)
    before = history.snapshot(override, ['free_device_limit', 'no_free_limit', 'allow_publishing',
                                         'studio_daily_cap_usd', 'notes']) if override.pk else {}
    form = _override_form(event, override, request.POST)
    if not form.is_valid():
        messages.error(request, 'Check the form: ' + '; '.join(f'{k}: {v[0]}' for k, v in form.errors.items()))
        return redirect('platform:event', event_id=event.pk)
    created = not override.pk
    override = form.save(commit=False)
    override.event, override.updated_by = event, request.user
    override.save()
    history.log(request.user, override, before, history.snapshot(override, list(before) or ['notes']),
                flag=history.ADDITION if created else history.CHANGE)
    d = form.cleaned_data
    old = history.snapshot(event, ['plan', 'paid', 'public', 'refresh_schedule'])
    event.paid = d['plan'] != 'free'
    event.plan = Event.PLAN_STUDIO if d['plan'] == 'studio' else Event.PLAN_STANDARD
    event.public = bool(d['listed'])
    event.refresh_schedule = 'watch' if d['watching'] else 'off'
    event.save(update_fields=['plan', 'paid', 'public', 'refresh_schedule'])
    history.log(request.user, event, old, history.snapshot(event, list(old)))
    if event.user_id:
        if d['owner_ai_unlimited'] or d['owner_ai_monthly_usd'] is not None:
            AiLimitOverride.objects.update_or_create(user=event.user, defaults={
                'unlimited': d['owner_ai_unlimited'], 'updated_by': request.user,
                'monthly_usd': None if d['owner_ai_unlimited'] else d['owner_ai_monthly_usd']})
        else:
            AiLimitOverride.objects.filter(user=event.user).delete()
    messages.success(request, f'Saved {event.name}.')
    return redirect('platform:event', event_id=event.pk)


@agent_action('platform_customer', 'One customer (superusers): their events with plans and payments, AI spend and what '
              'they used.', kind='read')
@superuser_required
def customer(request, user_id):
    from events.models import Event
    user = get_object_or_404(get_user_model(), pk=user_id)
    rows = data.customer_rows([u for u in data.customers(user.email) if u.pk == user.pk])
    events = [{'event': e, 'plan': plans.LABELS[plans.plan_of(e)]} for e in Event.objects.filter(user=user)]
    return render(request, 'platform_admin/customer.html', {
        'tabs': TABS, 'tab': 'customers', 'customer': user, 'row': rows[0] if rows else None, 'events': events,
        'payments': PaymentRecord.objects.filter(event__user=user).select_related('event')})


@superuser_required
def customers_csv(request):
    rows = data.customer_rows(data.customers(request.GET.get('q', ''), limit=100000))
    from prospects.views import csv_safe
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = 'attachment; filename="attnd-customers.csv"'
    w = csv.writer(response)
    features = list(rows[0]['features']) if rows else []
    w.writerow(['email', 'joined', 'last_active', 'events', 'plans', 'paid_usd', 'ai_month_usd', 'ai_total_usd',
                'ai_limit', *features])
    for r in rows:
        w.writerow([csv_safe(r['email']), r['joined'].date().isoformat(),
                    r['last_active'].date().isoformat() if r['last_active'] else '', r['events'], r['plans'],
                    r['paid_usd'], f'{r["ai_month"]:.2f}', f'{r["ai_total"]:.2f}', r['ai_limit'],
                    *[r['features'][f] for f in features]])
    return response
