"""Buying and managing tickets on attnd.app (the only place tickets are sold): ticket page → 10-minute hold →
details → Stripe Checkout → confirmation; tickets with QR and Wallet buttons; order lookup by email code; transfers,
resends and refund requests. No account needed.
"""

import hmac
import logging

from django.conf import settings
from django.contrib import messages
from django.http import Http404, HttpResponse, HttpResponseBadRequest, HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.clickjacking import xframe_options_exempt
from django.views.decorators.csp import csp_override
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from attnd.ratelimit import RateLimit, client_ip
from events.apis import live_events
from tickets import buyers, holders, inventory, money, payments, signing, stripe_api, wallet, waitlist
from tickets.models import Order, Ticket, TicketType, VerificationCode, normalize_email

logger = logging.getLogger(__name__)

SESSION_EMAIL = 'tickets_email'
SESSION_EMAIL_AT = 'tickets_email_at'
SESSION_DAYS = 7


def _event(code):
    event = live_events().filter(event_code__iexact=code).select_related('ticketing').first()
    if event is None or not inventory.plan_allows(event):
        raise Http404
    ts = inventory.ticketing(event)
    if not ts.enabled:
        raise Http404
    return event, ts


def _unlock_key(event):
    return f'tickets_unlock_{event.pk}'


def _rows(event, codes):
    rows = []
    for row in inventory.listing(event, codes):
        t = row['type']
        row['all_in'] = money.fmt(row['all_in_cents'], event.ticketing.currency)
        row['price'] = money.fmt(t.price_cents, event.ticketing.currency)
        row['price_input'] = money.dollars(t.price_cents)
        row['few_left'] = row['remaining'] is not None and 0 < row['remaining'] <= 20
        row['max'] = min(t.max_per_order, row['remaining'] if row['remaining'] is not None else t.max_per_order)
        rows.append(row)
    return rows


def ticket_page(request, event_code):
    event, ts = _event(event_code)
    codes = list(request.session.get(_unlock_key(event), []))
    if request.GET.get('code'):
        codes.append(request.GET['code'].strip().upper()[:32])
        request.session[_unlock_key(event)] = codes[-5:]
    state, reason = inventory.sales_state(event, ts)
    error = None
    if request.method == 'POST':
        if request.POST.get('action') == 'unlock':
            code = request.POST.get('unlock', '').strip().upper()[:32]
            if code and TicketType.objects.filter(event=event, hidden=True, unlock_code__iexact=code, active=True).exists():
                request.session[_unlock_key(event)] = (codes + [code])[-5:]
                messages.success(request, 'Unlocked.')
            else:
                messages.error(request, 'That code doesn’t unlock any tickets.')
            return redirect('tickets:page', event_code=event.event_code)
        limit = RateLimit('tickets_hold_ip')
        ip = client_ip(request)
        if limit.exceeded(ip):
            error = 'Too many carts from your network. Please wait a few minutes.'
        else:
            limit.hit(ip)
            lines = [(t.id, request.POST.get(f'qty_{t.id}') or 0, request.POST.get(f'amount_{t.id}'))
                     for t in TicketType.objects.filter(event=event, active=True)]
            source = request.POST.get('src', 'web')
            try:
                order = inventory.create_hold(event, lines, promo_code=request.POST.get('promo', ''),
                                              unlock_codes=codes, source=source if source in ('web', 'site', 'embed',
                                                                                              'app') else 'web',
                                              email=request.session.get(SESSION_EMAIL, ''))
                return redirect(payments.order_url(order))
            except inventory.CartError as exc:
                error = str(exc)
    rows = _rows(event, codes) if state in ('open', 'paused') else []
    return render(request, 'tickets/page.html', {
        'event': event, 'ts': ts, 'rows': rows, 'state': state, 'reason': reason, 'error': error,
        'src': request.GET.get('src', 'web'), 'has_hidden': TicketType.objects.filter(
            event=event, hidden=True, active=True).exists(),
        'has_promos': event.promo_codes.filter(active=True).exists(),
        'fee_note': f'{settings.TICKETS_FEE_PERCENT}% + {money.fmt(settings.TICKETS_FEE_FIXED_CENTS)}',
    }, status=400 if error else 200)


@xframe_options_exempt
@csp_override({'frame-ancestors': ['*']})
def embed(request, event_code):
    """A small ticket list for organizers' own websites (an iframe); buying opens attnd.app in a new tab."""
    event, ts = _event(event_code)
    state, reason = inventory.sales_state(event, ts)
    return render(request, 'tickets/embed.html', {'event': event, 'ts': ts, 'state': state, 'reason': reason,
                                                  'rows': _rows(event, []) if state == 'open' else [],
                                                  'url': reverse('tickets:page', args=[event.event_code])})


@require_POST
def join_waitlist(request, event_code):
    event, ts = _event(event_code)
    t = get_object_or_404(TicketType, pk=request.POST.get('type'), event=event, active=True, waitlist=True)
    email = normalize_email(request.POST.get('email'))
    limit = RateLimit('tickets_action_ip')
    if limit.exceeded(client_ip(request)) or not buyers.valid_email(email):
        messages.error(request, 'Enter a valid email address.' if not buyers.valid_email(email) else
                       'Too many requests. Please try again later.')
    else:
        limit.hit(client_ip(request))
        waitlist.join(t, email, request.POST.get('name', ''))
        messages.success(request, f'You’re on the waitlist for {t.name}. We’ll email you if tickets free up.')
    return redirect('tickets:page', event_code=event.event_code)


# ---------------------------------------------------------------------------
# Orders
# ---------------------------------------------------------------------------

def _order(public_id, key):
    order = Order.objects.filter(public_id=public_id).select_related('event', 'event__ticketing', 'promo').first()
    if order is None or not key or not hmac.compare_digest(order.key, key):
        raise Http404
    return order


def _holder_count(order):
    return sum(order.items.values_list('quantity', flat=True))


def _questions(ts):
    return [q for q in (ts.questions or []) if isinstance(q, dict) and q.get('id') and q.get('label')]


def _read_details(request, order, ts):
    """Validate the details form → (email, name, answers) or raise CartError."""
    email = normalize_email(request.POST.get('email'))
    name = request.POST.get('name', '').strip()[:200]
    if not buyers.valid_email(email):
        raise inventory.CartError('Enter a valid email address: your tickets go there.')
    if not name:
        raise inventory.CartError('Enter your name.')
    answers, holders_data = {}, []
    questions = _questions(ts)
    for q in questions:
        if q.get('per_ticket'):
            continue
        value = request.POST.get(f'q_{q["id"]}', '').strip()[:500]
        if q.get('required') and not value:
            raise inventory.CartError(f'Please answer: {q["label"]}')
        answers[q['id']] = value
    for i in range(_holder_count(order)):
        holder = {'name': request.POST.get(f't{i}_name', '').strip()[:200] or name, 'answers': {}}
        for q in questions:
            if not q.get('per_ticket'):
                continue
            value = request.POST.get(f't{i}_q_{q["id"]}', '').strip()[:500]
            if q.get('required') and not value:
                raise inventory.CartError(f'Ticket {i + 1}: please answer “{q["label"]}”.')
            holder['answers'][q['id']] = value
        holders_data.append(holder)
    if request.POST.get('terms') != '1':
        raise inventory.CartError('Please accept the terms of sale.')
    answers['_holders'] = holders_data
    return email, name, answers


def order_page(request, public_id, key):
    order = _order(public_id, key)
    event, ts = order.event, inventory.ticketing(order.event)
    now = timezone.now()
    if order.status in Order.ACTIVE_HOLDS and order.expires_at and order.expires_at <= now:
        inventory.expire_holds(now)
        order.refresh_from_db()
    error = None
    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'cancel':
            inventory.cancel_hold(order)
            return redirect('tickets:page', event_code=event.event_code)
        if action == 'pay' and order.status in Order.ACTIVE_HOLDS:
            try:
                if order.status == Order.HELD:
                    email, name, answers = _read_details(request, order, ts)
                    order.email, order.name, order.answers = email, name, answers
                    order.save(update_fields=['email', 'name', 'answers', 'updated_at'])
                    request.session[SESSION_EMAIL] = email
                if order.total_cents == 0:
                    payments.complete_free(order)
                    return redirect(payments.order_url(order))
                return HttpResponseRedirect(payments.start_checkout(order))
            except (inventory.CartError, payments.PaymentError) as exc:
                error = str(exc)
            except stripe_api.StripeError:
                logger.exception('Checkout failed for order %s', order.ref)
                error = 'We couldn’t reach the payment page. Please try again.'
    tickets = list(order.tickets.select_related('ticket_type')) if order.status in (Order.PAID, Order.REFUNDED) else []
    allowed, automatic, policy_message = payments.refund_policy(order) if order.status == Order.PAID else (False, False, '')
    return render(request, 'tickets/order.html', {
        'order': order, 'event': event, 'ts': ts, 'error': error,
        'items': list(order.items.select_related('ticket_type')),
        'tickets': tickets, 'questions': _questions(ts), 'holder_range': range(_holder_count(order)),
        'subtotal': money.fmt(order.subtotal_cents, order.currency),
        'discount': money.fmt(order.discount_cents, order.currency),
        'fee': money.fmt(order.fee_cents, order.currency), 'total': money.fmt(order.total_cents, order.currency),
        'just_paid': request.GET.get('paid') == '1', 'refund_allowed': allowed, 'refund_automatic': automatic,
        'refund_message': policy_message, 'apple': wallet.apple_enabled(), 'google': wallet.google_enabled(),
        'seconds_left': max(0, int((order.expires_at - now).total_seconds())) if order.expires_at else 0,
        'pending_request': order.refund_requests.filter(status='pending').exists(),
    }, status=400 if error else 200)


def _action_limited(request):
    limit = RateLimit('tickets_action_ip')
    if limit.exceeded(client_ip(request)):
        messages.error(request, 'Too many requests. Please try again later.')
        return True
    limit.hit(client_ip(request))
    return False


@require_POST
def order_resend(request, public_id, key):
    order = _order(public_id, key)
    if not _action_limited(request):
        try:
            holders.resend(order)
            messages.success(request, f'We sent the tickets to {order.email} again.')
        except holders.HolderError as exc:
            messages.error(request, str(exc))
    return redirect(payments.order_url(order))


@require_POST
def order_refund(request, public_id, key):
    order = _order(public_id, key)
    if not _action_limited(request):
        try:
            result = holders.request_refund(order, reason=request.POST.get('reason', ''))
            if hasattr(result, 'amount_cents'):
                messages.success(request, f'Refunded {money.fmt(result.amount_cents + result.fee_cents)}. It reaches '
                                          f'your card in 5–10 business days.')
            else:
                messages.success(request, 'We sent your request to the organizer.')
        except (holders.HolderError, payments.PaymentError) as exc:
            messages.error(request, str(exc))
    return redirect(payments.order_url(order))


# ---------------------------------------------------------------------------
# Tickets
# ---------------------------------------------------------------------------

def _ticket(public_id, key):
    ticket = Ticket.objects.filter(public_id=public_id).select_related('event', 'ticket_type', 'order').first()
    if ticket is None or not hmac.compare_digest(ticket.key, key or ''):
        raise Http404
    return ticket


def ticket_page_view(request, public_id, key):
    ticket = _ticket(public_id, key)
    if request.method == 'POST' and request.POST.get('action') == 'transfer' and not _action_limited(request):
        try:
            ticket = holders.transfer(ticket, request.POST.get('email', ''), request.POST.get('name', ''),
                                      from_name=ticket.holder_name or ticket.order.name)
            messages.success(request, f'Sent to {ticket.holder_email}. This link and the old QR code no longer work.')
            return redirect('tickets:page', event_code=ticket.event.event_code)
        except holders.HolderError as exc:
            messages.error(request, str(exc))
            return redirect('tickets:ticket', public_id=ticket.public_id, key=ticket.key)
    return render(request, 'tickets/ticket.html', {
        'ticket': ticket, 'event': ticket.event, 'valid': ticket.status == Ticket.VALID,
        'apple': wallet.apple_enabled(), 'google': wallet.google_enabled(),
        'can_transfer': ticket.status == Ticket.VALID and ticket.ticket_type.transferable and not ticket.checked_in_at,
        'order_link': payments.order_url(ticket.order) if ticket.holder_email == ticket.order.email else None})


def ticket_qr(request, public_id, key):
    ticket = _ticket(public_id, key)
    if ticket.status != Ticket.VALID:
        raise Http404
    import qrcode
    import qrcode.image.svg
    img = qrcode.make(signing.token_for(ticket), image_factory=qrcode.image.svg.SvgPathImage, box_size=10, border=2,
                      error_correction=qrcode.constants.ERROR_CORRECT_M)
    response = HttpResponse(img.to_string(), content_type='image/svg+xml')
    response['Cache-Control'] = 'private, no-store'
    return response


def apple_pass(request, public_id, key):
    ticket = _ticket(public_id, key)
    if not wallet.apple_enabled() or ticket.status != Ticket.VALID:
        raise Http404
    data, wallet_pass = wallet.apple_pkpass(ticket)
    response = HttpResponse(data, content_type='application/vnd.apple.pkpass')
    response['Content-Disposition'] = f'attachment; filename="ticket-{ticket.code}.pkpass"'
    response['Cache-Control'] = 'private, no-store'
    return response


def google_pass(request, public_id, key):
    ticket = _ticket(public_id, key)
    if not wallet.google_enabled() or ticket.status != Ticket.VALID:
        raise Http404
    return HttpResponseRedirect(wallet.google_save_url(ticket))


# ---------------------------------------------------------------------------
# Order lookup by email code ("Find my tickets")
# ---------------------------------------------------------------------------

def _session_email(request):
    email, at = request.session.get(SESSION_EMAIL), request.session.get(SESSION_EMAIL_AT)
    if not email or not at or timezone.now().timestamp() - at > SESSION_DAYS * 86400:
        return None
    return email


def lookup(request):
    step, email, error = 'email', normalize_email(request.POST.get('email') or request.GET.get('email')), None
    if request.method == 'POST':
        ip = client_ip(request)
        try:
            if request.POST.get('action') == 'verify':
                buyers.check_code(email, request.POST.get('code'), VerificationCode.PURPOSE_BUYER, ip)
                request.session.cycle_key()
                request.session[SESSION_EMAIL] = email
                request.session[SESSION_EMAIL_AT] = timezone.now().timestamp()
                return redirect('tickets:mine')
            buyers.send_code(email, VerificationCode.PURPOSE_BUYER, ip)
            step = 'code'
        except buyers.CodeError as exc:
            error = str(exc)
            step = 'code' if request.POST.get('action') == 'verify' else 'email'
    return render(request, 'tickets/lookup.html', {'step': step, 'email': email, 'error': error},
                  status=400 if error else 200)


def mine(request):
    email = _session_email(request)
    if email is None:
        return redirect('tickets:lookup')
    if request.method == 'POST':
        request.session.pop(SESSION_EMAIL, None)
        request.session.pop(SESSION_EMAIL_AT, None)
        return redirect('tickets:lookup')
    tickets = (Ticket.objects.filter(holder_email=email).select_related('event', 'ticket_type', 'order')
               .order_by('-event__start_time', 'id'))
    orders = Order.objects.filter(email=email, status__in=[Order.PAID, Order.REFUNDED]).select_related('event')
    return render(request, 'tickets/mine.html', {'email': email, 'tickets': tickets, 'orders': orders})


# ---------------------------------------------------------------------------
# Stripe webhooks
# ---------------------------------------------------------------------------

@csrf_exempt
@require_POST
def stripe_webhook(request):
    try:
        evt = stripe_api.verify_webhook(request.body, request.headers.get('Stripe-Signature', ''))
    except stripe_api.SignatureError as exc:
        logger.warning('Stripe webhook refused: %s', exc)
        return HttpResponseBadRequest('bad signature')
    status, text = payments.process_webhook(evt)
    return HttpResponse(text, status=status, content_type='text/plain')
