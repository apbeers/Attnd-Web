"""Staff: the AI spend page (/console/ai-spend/): this month's AI spend, the live AI limits and per-person overrides.

Changing a limit or an override always asks (the page's confirm, Ask Attnd's confirm card) and is logged in History.
"""
import csv
from datetime import datetime, timezone as dt_timezone

from django.contrib import messages
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from agent.registry import agent_action, param as P
from credit import history, ledger
from credit.forms import AiLimitsForm, OverrideForm
from credit.models import AiLimitOverride, AiLimits, AiSpend
from diagnostics.views import staff_required

LIMIT_FIELDS = AiLimitsForm._meta.fields


def month_range(value):
    """'2026-11' → (start, end) in UTC; anything else → this month."""
    try:
        start = datetime.strptime(value or '', '%Y-%m').replace(tzinfo=dt_timezone.utc)
    except ValueError:
        start = ledger.month_start()
    return start, ledger.next_month(start)


def _limits_summary(ctx):
    changed = []
    current = AiLimits.load()
    for name in LIMIT_FIELDS:
        new = ctx.args.get(name)
        if new not in (None, '') and str(new) != str(getattr(current, name)):
            label = AiLimitsForm._meta.labels.get(name, name)
            changed.append(f'{label}: {getattr(current, name)} → {new}')
    return 'Change the AI limits for everyone: ' + ('; '.join(changed) if changed else 'no change')


def _override_summary(ctx):
    who = ctx.args.get('email') or 'this person'
    if ctx.args.get('unlimited'):
        return f'Give {who} no AI limit'
    return f'Set {who}\'s monthly AI credit to ${ctx.args.get("monthly_usd")}'


@agent_action('ai_spend', "This month's AI spend (staff): by feature, model, person and event; the AI limits and "
              'per-person overrides.', kind='read')
@staff_required
def spend_page(request):
    start, _ = month_range(request.GET.get('month'))
    return render(request, 'credit/spend.html', {'month': f'{start:%Y-%m}', 'month_label': f'{start:%B %Y}'})


@agent_action('save_ai_limits', 'Change the live AI limits (staff): the monthly credit per person, the visitor '
              'allowance, the platform alert and the safety caps per day.', confirm=True, summary=_limits_summary,
              form=AiLimitsForm, prefill='credit:spend', title='Changed the AI limits')
@staff_required
@require_POST
def save_limits(request):
    conf = AiLimits.load()
    if conf._state.adding:
        conf.save()
    before = history.snapshot(conf, LIMIT_FIELDS)
    form = AiLimitsForm(request.POST, instance=conf)
    if not form.is_valid():
        start = ledger.month_start()
        return render(request, 'credit/spend.html', {'month': f'{start:%Y-%m}', 'month_label': f'{start:%B %Y}',
                                                     'limits_form': form}, status=400)
    conf = form.save(commit=False)
    conf.updated_by = request.user
    conf.save()
    changed = history.log(request.user, conf, before, history.snapshot(conf, LIMIT_FIELDS))
    messages.success(request, 'Saved the AI limits.' if changed else 'Nothing changed.')
    return redirect('credit:spend')


@agent_action('set_ai_limit_override', "Give one person their own monthly AI credit, or no limit (staff).",
              confirm=True, summary=_override_summary, form=OverrideForm, title='Set an AI credit override')
@staff_required
@require_POST
def save_override(request):
    form = OverrideForm(request.POST)
    if not form.is_valid():
        for field, errors in form.errors.items():
            messages.error(request, f'{form.fields[field].label if field in form.fields else "Override"}: {errors[0]}')
        return redirect('credit:spend')
    found = AiLimitOverride.objects.filter(user=form.user).first()
    before = history.snapshot(found, ['monthly_usd', 'unlimited', 'note']) if found else {}
    override, created = AiLimitOverride.objects.update_or_create(user=form.user, defaults={
        'monthly_usd': None if form.cleaned_data['unlimited'] else form.cleaned_data['monthly_usd'],
        'unlimited': form.cleaned_data['unlimited'], 'note': form.cleaned_data['note'], 'updated_by': request.user})
    history.log(request.user, override, before, history.snapshot(override, ['monthly_usd', 'unlimited', 'note']),
                flag=history.ADDITION if created else history.CHANGE)
    messages.success(request, f'{form.user.email}: {"no limit" if override.unlimited else f"${override.monthly_usd} a month"}.')
    return redirect('credit:spend')


@agent_action('remove_ai_limit_override', "Remove a person's own AI credit: they go back to the default (staff).",
              confirm=True, params={'override': P('integer', 'The override\'s id (from the AI spend page).')},
              title='Removed an AI credit override')
@staff_required
@require_POST
def remove_override(request):
    override = get_object_or_404(AiLimitOverride, pk=request.POST.get('override'))
    history.log(request.user, override, flag=history.DELETION)
    email = override.user.email
    override.delete()
    messages.success(request, f'{email} is back on the default AI credit.')
    return redirect('credit:spend')


@staff_required
def export_csv(request):
    start, end = month_range(request.GET.get('month'))
    from prospects.views import csv_safe
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="attnd-ai-spend-{start:%Y-%m}.csv"'
    writer = csv.writer(response)
    writer.writerow(['time_utc', 'person', 'visitor', 'event_code', 'feature', 'provider', 'model', 'input_tokens',
                     'output_tokens', 'cost_usd', 'counted', 'background', 'request_id'])
    rows = (AiSpend.objects.filter(created_at__gte=start, created_at__lt=end).select_related('user', 'event')
            .order_by('created_at').iterator(chunk_size=2000))
    for s in rows:
        writer.writerow([s.created_at.strftime('%Y-%m-%d %H:%M:%S'), csv_safe(s.user.email if s.user_id else ''),
                         'yes' if s.visitor else '', csv_safe(s.event.event_code if s.event_id else ''), s.feature,
                         s.provider, csv_safe(s.model), s.input_tokens, s.output_tokens, f'{s.cost_usd:.5f}',
                         'yes' if s.counted else 'no', 'yes' if s.background else 'no', csv_safe(s.request_id)])
    return response
