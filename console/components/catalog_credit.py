"""AI credit as components (credit/): the person's own meter (Account) and, for staff, the AI spend summary, the live
AI limits and per-person overrides (the AI spend page and the canvas's AI spend tab)."""

from decimal import Decimal

from django.db.models import Count, Sum
from django.http import Http404
from django.urls import reverse

from console.components import component, form_json
from console.components.catalog import fmt


def _staff(user):
    if not (getattr(user, 'is_authenticated', False) and user.has_perm('credit.view_aispend')):
        raise Http404


def _usd(value):
    value = Decimal(value or 0)
    return f'{value:,.2f}'


def _labels():
    from credit.ledger import FEATURE_LABELS
    return FEATURE_LABELS


@component('ai_credit_meter', 'AI credit', "The person's own AI credit this month: used, left, when it renews, and "
           'what used it (Ask Attnd, imports, Studio…).', ids=(), permission='signed_in', page=lambda ids: reverse('console:account') + '#ai-credit')
def ai_credit_meter(user):
    from credit import ledger
    from credit.models import AiSpend
    s = ledger.status(user)
    rows = (AiSpend.objects.filter(user=user, counted=True, created_at__gte=ledger.month_start())
            .values('feature').annotate(cost=Sum('cost_usd'), calls=Count('id')).order_by('-cost'))
    labels = _labels()
    return {'name': 'AI credit', 'used': _usd(s['used']), 'limit': '' if s['unlimited'] else f"{s['limit']:,.0f}",
            'left': '' if s['unlimited'] else _usd(s['left']), 'percent': s['percent'], 'unlimited': s['unlimited'],
            'renews': s['renews_label'], 'near': not s['unlimited'] and s['percent'] >= 80,
            'over': not s['unlimited'] and s['percent'] >= 100,
            'breakdown': [{'label': labels.get(r['feature'], r['feature'].replace('_', ' ').capitalize()),
                           'cost': _usd(r['cost']), 'calls': r['calls']} for r in rows]}


@component('ai_spend_summary', 'AI spend', "This month's AI spend across Attnd (staff only): total, against the alert "
           'amount, by feature, by model, the people and events that used most, the latest calls.', ids=(),
           audience='staff', permission='credit.view_aispend', page=lambda ids: reverse('credit:spend'))
def ai_spend_summary(user, month=None):
    from credit import ledger
    from credit.models import AiSpend
    from credit.views import month_range
    _staff(user)
    start, end = month_range(month)
    qs = AiSpend.objects.filter(created_at__gte=start, created_at__lt=end)
    total = qs.aggregate(t=Sum('cost_usd'))['t'] or Decimal('0')
    alert = Decimal(ledger.limits().platform_alert_usd or 0)
    labels = _labels()
    features = [{'label': labels.get(r['feature'], r['feature'].replace('_', ' ').capitalize()), 'cost': _usd(r['cost']),
                 'calls': r['calls'], 'share': int(r['cost'] / total * 100) if total else 0}
                for r in qs.values('feature').annotate(cost=Sum('cost_usd'), calls=Count('id')).order_by('-cost')]
    models = [{'label': f"{r['model'] or '—'} ({r['provider']})", 'cost': _usd(r['cost']), 'calls': r['calls']}
              for r in qs.values('provider', 'model').annotate(cost=Sum('cost_usd'), calls=Count('id')).order_by('-cost')[:8]]
    people = []
    for r in (qs.filter(user__isnull=False).values('user', 'user__email').annotate(cost=Sum('cost_usd'), calls=Count('id'))
              .order_by('-cost')[:10]):
        people.append({'email': r['user__email'], 'cost': _usd(r['cost']), 'calls': r['calls']})
    events = [{'name': r['event__name'], 'code': r['event__event_code'], 'cost': _usd(r['cost']),
               'url': reverse('console:event', args=[r['event']])}
              for r in (qs.filter(event__isnull=False).values('event', 'event__name', 'event__event_code')
                        .annotate(cost=Sum('cost_usd')).order_by('-cost')[:10])]
    visitors = qs.filter(user__isnull=True, visitor__gt='').aggregate(t=Sum('cost_usd'), n=Count('visitor', distinct=True))
    latest = [{'when': fmt(s.created_at, 'M j, g:i A'), 'who': s.user.email if s.user_id else ('Visitor' if s.visitor else 'Attnd'),
               'feature': labels.get(s.feature, s.feature), 'model': s.model, 'cost': f'{s.cost_usd:.4f}',
               'exempt': not s.counted, 'background': s.background,
               'event': s.event.name if s.event_id else '',
               'url': reverse('console:event', args=[s.event_id]) if s.event_id else ''}
              for s in qs.select_related('user', 'event').order_by('-created_at')[:15]]
    return {'name': 'AI spend', 'month': f'{start:%Y-%m}', 'month_label': f'{start:%B %Y}',
            'prev': f'{ledger.month_start(start.replace(day=1) - __import__("datetime").timedelta(days=1)):%Y-%m}',
            'next': f'{end:%Y-%m}' if end <= ledger.next_month() and start < ledger.month_start() else '',
            'total': _usd(total), 'alert': f'{alert:,.0f}', 'alert_percent': int(min(100, total / alert * 100)) if alert else 0,
            'exempt': _usd(qs.filter(counted=False).aggregate(t=Sum('cost_usd'))['t']),
            'calls': qs.count(), 'features': features, 'models': models, 'people': people, 'events': events,
            'visitors': {'cost': _usd(visitors['t']), 'count': visitors['n']}, 'latest': latest,
            'csv': reverse('credit:export') + f'?month={start:%Y-%m}'}


def _limits_bind(user, ids, data):
    from credit.forms import AiLimitsForm
    from credit.models import AiLimits
    _staff(user)
    form = AiLimitsForm(data, instance=AiLimits.load())
    form.is_valid()
    return form


@component('ai_limits_form', 'AI limits', 'The live AI limits (staff only): the monthly credit per person, the '
           'allowance before signing up, the platform alert, and the safety caps per day. Saving always asks.',
           ids=(), audience='staff', permission='credit.change_ailimits', actions=(('save_ai_limits', 'Save limits'),), form=_limits_bind,
           page=lambda ids: reverse('credit:spend') + '#limits')
def ai_limits_form(user, form=None):
    from credit.forms import AiLimitsForm
    from credit.models import AiLimits
    _staff(user)
    conf = AiLimits.load()
    form = form or AiLimitsForm(instance=conf)
    data = form_json(form, [('grid', 'monthly_default_usd', 'visitor_allowance_usd'), 'platform_alert_usd',
                            ('details', 'Safety caps per day', [('grid', 'agent_daily_usd', 'studio_daily_usd'),
                                                                ('grid', 'place_images_daily_usd', 'covers_daily_usd'),
                                                                ('grid', 'prospects_daily_usd', 'new_account_daily_imports')])],
                     action=reverse('credit:limits'), confirm_action='save_ai_limits',
                     submit=[{'label': 'Save limits', 'style': 'primary',
                              'confirm': 'Change the AI limits for everyone? It takes effect within a minute.'}])
    data['no_ai'] = True  # numbers a person decides, not something to draft with AI
    return {'name': 'AI limits', 'form': data, 'actions': [{'name': 'save_ai_limits'}],
            'updated': fmt(conf.updated_at, 'M j, g:i A') if conf.pk and conf.updated_at else '',
            'updated_by': conf.updated_by.email if conf.pk and conf.updated_by_id else ''}


@component('ai_limit_overrides', 'AI credit overrides', 'People with their own monthly AI credit or no limit (staff '
           'only), what they used this month; set one or go back to the default. Both always ask.', ids=(),
           audience='staff', permission='credit.change_ailimits', actions=(('set_ai_limit_override', 'Save override'), ('remove_ai_limit_override', 'Remove')),
           page=lambda ids: reverse('credit:spend') + '#overrides')
def ai_limit_overrides(user, form=None):
    from credit import ledger
    from credit.forms import OverrideForm
    from credit.models import AiLimitOverride
    from console.components import get, resolve_actions
    _staff(user)
    rows = []
    for o in AiLimitOverride.objects.select_related('user').order_by('user__email'):
        actions = [{'name': 'remove_ai_limit_override', 'style': 'outline', 'fields': {'override': o.pk},
                    'params': {'override': str(o.pk)}, 'confirm_text': f'Put {o.user.email} back on the default AI credit?'}]
        actions = [dict(a, **{k: v for k, v in r.items() if k != 'name'}) for a, r in
                   zip(actions, resolve_actions(get('ai_limit_overrides'), {}, actions))]
        rows.append({'id': o.pk, 'email': o.user.email, 'limit': 'No limit' if o.unlimited else f'${o.monthly_usd:,.2f}',
                     'used': _usd(ledger.used_this_month(o.user)), 'note': o.note, 'actions': actions})
    data = form_json(form or OverrideForm(), [('grid', 'email', 'monthly_usd'), 'unlimited', 'note'],
                     action=reverse('credit:override'), confirm_action='set_ai_limit_override',
                     submit=[{'label': 'Save override', 'style': 'primary', 'confirm': "Change this person's AI credit?"}])
    data['no_ai'] = True
    return {'name': 'AI credit overrides', 'rows': rows, 'form': data,
            'default': f'{ledger.limits().monthly_default_usd:,.0f}',
            'actions': [a for r in rows for a in r['actions']] + [{'name': 'set_ai_limit_override'}]}
