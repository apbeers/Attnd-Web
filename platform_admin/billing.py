"""Billing per event (batch 7): invoices, receipts, PDF, CSV, and Stripe Checkout + Customer Portal + webhooks that
stay off until the STRIPE_* keys exist ("Payments aren't set up yet").

- `issue(event, kind, user)` makes an open invoice at today's price (Pay now, or Attnd sending a bill).
- `mark_paid(invoice, method, user)` = the one place a payment lands: a `PaymentRecord`, the plan applied (paid,
  Studio), the invoice paid. Used by the Platform's manual payments, approved upgrade requests and Stripe's webhook.
- `pdf(invoice)` is a small hand-written PDF (no dependency): Attnd's details, the bill, paid or due.
- Stripe (`stripe` SDK, the only third-party addition allowed for this): `stripe_enabled()` needs
  `STRIPE_SECRET_KEY` and `STRIPE_WEBHOOK_SECRET`; `checkout_url(invoice)` makes a Checkout Session for the invoice,
  `portal_url(invoice)` the Customer Portal of the customer Checkout created, `handle_webhook(payload, signature)`
  verifies the signature and marks the invoice paid on `checkout.session.completed` (idempotent).
"""

import csv
import io
import logging

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

logger = logging.getLogger(__name__)

SELLER = ('Attnd', 'attnd.app')


def stripe_enabled():
    return bool(getattr(settings, 'STRIPE_SECRET_KEY', '') and getattr(settings, 'STRIPE_WEBHOOK_SECRET', ''))


def _stripe():
    import stripe
    stripe.api_key = settings.STRIPE_SECRET_KEY
    return stripe


def next_number(now=None):
    from platform_admin.models import Invoice
    year = (now or timezone.now()).year
    last = (Invoice.objects.filter(number__startswith=f'ATT-{year}-').order_by('-number')
            .values_list('number', flat=True).first())
    n = int(last.rsplit('-', 1)[1]) + 1 if last else 1
    return f'ATT-{year}-{n:04d}'


def _create(**fields):
    from platform_admin.models import Invoice
    for _ in range(5):  # two invoices at once: the unique number decides, the loser takes the next one
        try:
            with transaction.atomic():
                return Invoice.objects.create(number=next_number(), **fields)
        except IntegrityError:
            continue
    raise RuntimeError('Could not number the invoice')


def description(kind, event):
    from platform_admin import plans
    return f'Attnd {plans.LABELS[kind]} for “{event.name}” ({event.event_code})'


def issue(event, kind, user=None, amount=None, note=''):
    """An open invoice for one plan at the current price (or `amount`)."""
    from platform_admin import plans
    return _create(event=event, plan=kind, description=description(kind, event)[:200],
                   amount_usd=amount if amount is not None else plans.prices()[kind],
                   billed_to=(event.user.email if event.user_id and event.user else '')[:254], created_by=user,
                   note=(note or '')[:200])


def open_invoice(event, kind):
    from platform_admin.models import Invoice
    return Invoice.objects.filter(event=event, plan=kind, status=Invoice.OPEN).first()


def mark_paid(invoice, method, user=None, note=''):
    """Record the payment, apply the plan, close the invoice. Safe to call twice (a webhook retried)."""
    from events.models import Event
    from platform_admin.models import Invoice, PaymentRecord
    with transaction.atomic():
        invoice = Invoice.objects.select_for_update().get(pk=invoice.pk)
        if invoice.status == Invoice.PAID:
            return invoice
        payment = PaymentRecord.objects.create(event=invoice.event, plan=invoice.plan, amount_usd=invoice.amount_usd,
                                               recorded_by=user, note=(note or f'Invoice {invoice.number}')[:200])
        invoice.status, invoice.method, invoice.paid_at, invoice.payment = Invoice.PAID, method, timezone.now(), payment
        invoice.save(update_fields=['status', 'method', 'paid_at', 'payment'])
        event = invoice.event
        event.paid = True
        if invoice.plan == 'studio':
            event.plan = Event.PLAN_STUDIO
        event.save(update_fields=['paid', 'plan'])
    return invoice


def record_manual(event, kind, amount, user, note=''):
    """The Platform's "Record a payment": the same invoice row as Stripe, already paid."""
    invoice = open_invoice(event, kind) or issue(event, kind, user, amount=amount, note=note)
    if invoice.amount_usd != amount:
        invoice.amount_usd = amount
        invoice.save(update_fields=['amount_usd'])
    return mark_paid(invoice, invoice.MANUAL, user, note=note)


# ---------------------------------------------------------------------------
# Stripe (off until the keys exist)
# ---------------------------------------------------------------------------

def checkout_url(invoice, success_url, cancel_url):
    stripe = _stripe()
    session = stripe.checkout.Session.create(
        mode='payment', client_reference_id=invoice.number, customer_creation='always',
        customer_email=invoice.billed_to or None, success_url=success_url, cancel_url=cancel_url,
        metadata={'invoice': invoice.number, 'event_id': str(invoice.event_id)},
        line_items=[{'quantity': 1, 'price_data': {
            'currency': 'usd', 'unit_amount': int(round(invoice.amount_usd * 100)),
            'product_data': {'name': invoice.description[:250]}}}])
    invoice.stripe_session = session.id
    invoice.save(update_fields=['stripe_session'])
    return session.url


def portal_url(invoice, return_url):
    if not invoice.stripe_customer:
        return ''
    stripe = _stripe()
    return stripe.billing_portal.Session.create(customer=invoice.stripe_customer, return_url=return_url).url


def handle_webhook(payload, signature):
    """True when handled (or ignored); raises ValueError on a bad signature."""
    from platform_admin.models import Invoice
    stripe = _stripe()
    try:
        event = stripe.Webhook.construct_event(payload, signature, settings.STRIPE_WEBHOOK_SECRET)
    except Exception as exc:  # stripe.SignatureVerificationError, ValueError
        raise ValueError('bad signature') from exc
    if event['type'] != 'checkout.session.completed':
        return True
    session = event['data']['object']
    number = (session.get('metadata') or {}).get('invoice') or session.get('client_reference_id')
    invoice = Invoice.objects.filter(number=number).first()
    if invoice is None:
        logger.warning('Stripe checkout for an unknown invoice %s', number)
        return True
    if session.get('payment_status') not in ('paid', 'no_payment_required'):
        return True
    if session.get('customer') and not invoice.stripe_customer:
        invoice.stripe_customer = session['customer']
        invoice.save(update_fields=['stripe_customer'])
    mark_paid(invoice, Invoice.STRIPE, None, note=f'Stripe {session.get("id", "")}'[:200])
    return True


# ---------------------------------------------------------------------------
# PDF, receipt, CSV
# ---------------------------------------------------------------------------

def _pdf_text(value):
    text = str(value).replace('“', '"').replace('”', '"').replace('’', "'").replace('·', '-').replace('–', '-')
    text = text.encode('latin-1', 'replace').decode('latin-1')
    return text.replace('\\', '\\\\').replace('(', '\\(').replace(')', '\\)')


def pdf(invoice):
    """A one-page A4 PDF (Helvetica, text only)."""
    paid = invoice.status == invoice.PAID
    when = timezone.localtime(invoice.issued_at).strftime('%B %-d, %Y')
    lines = [
        (20, 'Attnd', True), (11, 'attnd.app', False), (11, '', False),
        (16, ('Receipt' if paid else 'Invoice') + f' {invoice.number}', True),
        (11, f'Issued {when}', False),
        (11, f'Billed to: {invoice.billed_to or "the event owner"}', False), (11, '', False),
        (12, invoice.description, False),
        (12, f'Amount: ${invoice.amount_usd:,.2f} USD', True),
        (11, '', False),
        (12, (f'Paid {timezone.localtime(invoice.paid_at).strftime("%B %-d, %Y")} - {invoice.get_method_display()}'
              if paid else ('Void' if invoice.status == invoice.VOID else 'Due on receipt')), False),
    ]
    if invoice.note:
        lines.append((10, f'Note: {invoice.note}', False))
    ops, y = [], 790
    for size, text, bold in lines:
        if text:
            ops.append(f'BT /{"F2" if bold else "F1"} {size} Tf 56 {y} Td ({_pdf_text(text)}) Tj ET')
        y -= size + 10
    stream = '\n'.join(ops).encode('latin-1')
    objects = [
        b'<< /Type /Catalog /Pages 2 0 R >>',
        b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
        b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 4 0 R /F2 5 0 R >> >> '
        b'/Contents 6 0 R >>',
        b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>',
        b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>',
        b'<< /Length ' + str(len(stream)).encode() + b' >>\nstream\n' + stream + b'\nendstream',
    ]
    out = io.BytesIO()
    out.write(b'%PDF-1.4\n')
    offsets = []
    for i, body in enumerate(objects, 1):
        offsets.append(out.tell())
        out.write(f'{i} 0 obj\n'.encode() + body + b'\nendobj\n')
    xref = out.tell()
    out.write(f'xref\n0 {len(objects) + 1}\n0000000000 65535 f \n'.encode())
    for off in offsets:
        out.write(f'{off:010d} 00000 n \n'.encode())
    out.write(f'trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n'.encode())
    return out.getvalue()


def send_receipt(invoice, to):
    """Email the receipt (or invoice) with its PDF to someone."""
    from django.core.mail import EmailMessage
    kind = 'Receipt' if invoice.status == invoice.PAID else 'Invoice'
    body = (f'{kind} {invoice.number} from Attnd\n\n{invoice.description}\nAmount: ${invoice.amount_usd:,.2f} USD\n'
            + ('Paid, thank you.\n' if invoice.status == invoice.PAID else 'Due on receipt.\n'))
    message = EmailMessage(f'{kind} {invoice.number} · {invoice.event.name}', body, None, [to])
    message.attach(f'{invoice.number}.pdf', pdf(invoice), 'application/pdf')
    message.send(fail_silently=False)


def csv_export(invoices):
    from prospects.views import csv_safe
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(['number', 'issued', 'description', 'amount_usd', 'status', 'paid_at', 'method', 'billed_to'])
    for inv in invoices:
        writer.writerow([csv_safe(v) for v in (inv.number, inv.issued_at.date().isoformat(), inv.description,
                                               f'{inv.amount_usd:.2f}', inv.get_status_display(),
                                               inv.paid_at.date().isoformat() if inv.paid_at else '',
                                               inv.get_method_display() if inv.paid_at else '', inv.billed_to)])
    return out.getvalue()


__all__ = ['issue', 'mark_paid', 'record_manual', 'pdf', 'send_receipt', 'csv_export', 'stripe_enabled',
           "checkout_url", "portal_url", "handle_webhook"]
