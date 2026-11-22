"""An event's Billing page (batch 7): plan, payments, invoices (number, date, amount, PDF), Pay now, Upgrade, send a
receipt, export. Stripe is wired but off until STRIPE_* exist (platform_admin.billing); the webhook is public and
verified by Stripe's signature."""

from django import forms
from django.contrib import messages
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from agent import summaries as S
from agent.registry import agent_action, param as P
from console.views_events import event_view
from attnd.public import public
from platform_admin import billing, plans
from platform_admin.models import Invoice

MANAGE = 'events.manage_event'


class ReceiptForm(forms.Form):
    email = forms.EmailField(label='Send to', help_text='We email the receipt with its PDF.')


def _invoice(event, number):
    return get_object_or_404(Invoice, event=event, number=number)


@agent_action('event_billing', "An event's billing: its plan, invoices (number, date, amount, PDF), Pay now, upgrade "
              'and receipts (owner and event admins).', kind='read')
@event_view(perm=MANAGE)
def event_billing(request, event):
    from console.views_events import _ctx
    return render(request, 'console/event_billing.html', _ctx(event, 'billing'))


@agent_action('pay_invoice', "Pay an event's plan now: opens Stripe Checkout for an open invoice (made if needed). "
              "Before payments are set up it says so.", json=True,
              params={'plan': P('string', 'standard or studio')}, title='Started a payment')
@event_view(perm=MANAGE)
@require_POST
def pay_now(request, event):
    kind = request.POST.get('plan') if request.POST.get('plan') in (plans.STANDARD, plans.STUDIO) else plans.STANDARD
    wants_json = 'application/json' in request.headers.get('Accept', '')
    if not billing.stripe_enabled():
        text = "Payments aren't set up yet. Use Remove the limit / Upgrade and Attnd will invoice you."
        if wants_json:
            return JsonResponse({'error': text}, status=409)
        messages.info(request, text)
        return redirect('console:event_billing', event_id=event.pk)
    invoice = billing.open_invoice(event, kind) or billing.issue(event, kind, request.user)
    back = request.build_absolute_uri(reverse('console:event_billing', args=[event.pk]))
    try:
        url = billing.checkout_url(invoice, success_url=back + '?paid=1', cancel_url=back)
    except Exception:
        text = 'Payments are unavailable right now. Please try again in a few minutes.'
        if wants_json:
            return JsonResponse({'error': text}, status=502)
        messages.error(request, text)
        return redirect('console:event_billing', event_id=event.pk)
    if wants_json:
        return JsonResponse({'url': url})
    return redirect(url)


@agent_action('event_invoice_pdf', "One invoice or receipt as a PDF.", kind='read')
@event_view(perm=MANAGE)
def invoice_pdf(request, event, number):
    invoice = _invoice(event, number)
    response = HttpResponse(billing.pdf(invoice), content_type='application/pdf')
    response['Content-Disposition'] = f'inline; filename="{invoice.number}.pdf"'
    return response


@agent_action('event_invoices_csv', "Every invoice of the event as CSV.", kind='read')
@event_view(perm=MANAGE)
def invoices_csv(request, event):
    response = HttpResponse(billing.csv_export(event.invoices.all()), content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="{event.event_code}-invoices.csv"'
    return response


@agent_action('send_receipt', 'Email an invoice or receipt (with its PDF) to someone.', confirm=True,
              form=ReceiptForm, params={'invoice': P('string', 'the invoice number, e.g. ATT-2026-0001')},
              summary=S.send_receipt, title='Sent a receipt')
@event_view(perm=MANAGE)
@require_POST
def send_receipt(request, event):
    invoice = _invoice(event, request.POST.get('invoice', ''))
    form = ReceiptForm(request.POST)
    if not form.is_valid():
        messages.error(request, 'Enter an email address to send it to.')
    else:
        billing.send_receipt(invoice, form.cleaned_data['email'])
        messages.success(request, f'Sent {invoice.number} to {form.cleaned_data["email"]}.')
    return redirect('console:event_billing', event_id=event.pk)


@agent_action('billing_portal', "Open Stripe's customer portal for the event's card payments (receipts, card).",
              json=True, params={'invoice': P('string', 'a paid invoice number')}, title='Opened the billing portal')
@event_view(perm=MANAGE)
@require_POST
def portal(request, event):
    invoice = _invoice(event, request.POST.get('invoice', ''))
    if not billing.stripe_enabled() or not invoice.stripe_customer:
        raise Http404
    url = billing.portal_url(invoice, request.build_absolute_uri(reverse('console:event_billing', args=[event.pk])))
    if 'application/json' in request.headers.get('Accept', ''):
        return JsonResponse({'url': url})
    return redirect(url)


@public
@csrf_exempt
@require_POST
def stripe_webhook(request):
    if not billing.stripe_enabled():
        raise Http404
    try:
        billing.handle_webhook(request.body, request.headers.get('Stripe-Signature', ''))
    except ValueError:
        return HttpResponse(status=400)
    return HttpResponse(status=200)
