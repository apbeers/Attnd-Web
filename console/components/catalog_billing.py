"""An event's billing as a component (batch 7): the Billing page and the canvas Settings tab."""

from django.urls import reverse
from django.utils import timezone

from console.components import component, form_json
from console.components.catalog import _tz, editable_event, fmt


@component('event_billing', 'Billing', "An event's plan and payments: Pay now (Stripe, once set up) or Upgrade, every "
           'invoice with its number, date, amount and PDF, Send a receipt (always asks) and the CSV export. Owner and '
           'event admins.', actions=(('pay_invoice', 'Pay now'), ('request_upgrade', 'Upgrade'),
                                     ('send_receipt', 'Send a receipt')),
           page=lambda ids: reverse('console:event_billing', args=[ids['event_id']]))
def event_billing(user, event_id=None, event=None):
    from django.db.models import Sum
    from django.http import Http404

    from console.views_billing import ReceiptForm
    from platform_admin import billing, plans
    event = editable_event(user, event_id, event)
    if not event.can_manage_team(user):
        raise Http404
    kind = plans.plan_of(event)
    actions = []
    if billing.stripe_enabled() and kind != plans.STUDIO:
        target = plans.STANDARD if kind == plans.FREE else plans.STUDIO
        actions.append({'name': 'pay_invoice', 'style': 'primary', 'fields': {'plan': target},
                        'label': f'Pay now · {plans.LABELS[target]} {plans.price_label(target)}'})
    if kind != plans.STUDIO:
        actions.append({'name': 'request_upgrade', 'style': 'outline', 'fields': {'kind': 'studio'},
                        'label': f'Upgrade to Studio · {plans.price_label("studio")}'})
    invoices = []
    with timezone.override(_tz(event)):
        for inv in event.invoices.all()[:50]:
            invoices.append({'number': inv.number, 'date': fmt(inv.issued_at, 'M j, Y'), 'description': inv.description,
                             'amount': f'{inv.amount_usd:,.2f}', 'status': inv.status,
                             'pdf': reverse('console:event_invoice_pdf', args=[event.pk, inv.number])})
    receipt = None
    if invoices:
        receipt = form_json(ReceiptForm(), ['email'], action=reverse('console:event_send_receipt', args=[event.pk]),
                            submit=[{'label': 'Send', 'style': 'outline'}], confirm_action='send_receipt')
        receipt['hidden'] = {'invoice': invoices[0]['number']}
        receipt['no_ai'] = True
    paid = event.payments.aggregate(total=Sum('amount_usd'))['total']
    return {'event_id': event.pk, 'name': f'Billing · {event.name}', 'plan': plans.LABELS[kind],
            'stripe': billing.stripe_enabled(), 'invoices': invoices, 'receipt_form': receipt,
            'paid_total': f'{paid:,.2f}' if paid else '', 'actions': actions,
            'csv': reverse('console:event_invoices_csv', args=[event.pk])}


@component('billing_queue', 'Upgrade requests and payments', 'Platform admins: pending upgrade and "remove the limit" '
           'requests (event, plan, price, who asked) and the latest payments recorded by hand or through Stripe. Read '
           'only; approve or decline on the Platform page.', ids=(), permission='platform_admin.change_platformsettings',
           audience='superuser',
           chat_only='The Platform page draws these lists itself; the chat and MCP show this read-only summary.')
def billing_queue(user):
    from django.http import Http404

    from platform_admin import plans
    from platform_admin.models import PaymentRecord, UpgradeRequest
    if not user.has_perm('platform_admin.change_platformsettings'):
        raise Http404
    requests = []
    for r in UpgradeRequest.objects.filter(status=UpgradeRequest.PENDING).select_related('event', 'requested_by')[:50]:
        requests.append({'id': r.pk, 'event': r.event.name, 'event_id': r.event_id, 'code': r.event.event_code,
                         'kind': r.get_kind_display(), 'price': f'{r.price_usd:,.2f}',
                         'by': r.requested_by.email if r.requested_by_id else '',
                         'when': fmt(r.created_at, 'M j, Y'), 'message': r.message[:300],
                         'url': reverse('platform:event', args=[r.event_id])})
    payments = []
    for p in PaymentRecord.objects.select_related('event', 'recorded_by').order_by('-paid_at')[:20]:
        payments.append({'event': p.event.name, 'event_id': p.event_id, 'plan': plans.LABELS.get(p.plan, p.plan),
                         'amount': f'{p.amount_usd:,.2f}', 'when': fmt(p.paid_at, 'M j, Y'), 'note': p.note,
                         'by': p.recorded_by.email if p.recorded_by_id else 'Stripe',
                         'url': reverse('platform:event', args=[p.event_id])})
    return {'name': 'Upgrade requests and payments', 'requests': requests, 'payments': payments,
            'page': reverse('platform:home')}
