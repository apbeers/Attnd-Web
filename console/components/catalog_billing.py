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
