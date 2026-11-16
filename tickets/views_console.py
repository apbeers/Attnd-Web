"""Organizer tools for tickets (console, /console/events/<id>/tickets/…).

Ticketing is part of the $499 Studio + tickets plan and, like Studio, invite-only for now: people without Studio
access get 404 and see no entry point. Everyone on the event's team can look; money actions (prices, refunds, comps,
payouts, cancelling, door staff, emails to holders) are for the owner and event admins.
"""

import csv
import logging
from functools import wraps

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q, Sum
from django.http import Http404, HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from console.views_events import _ctx, _tz
from events.content import mark_locked
from events.models import Event
from studio.access import cached_access, can_switch_plan
from tickets import (checkin, emails, forms, holders, inventory, money, payments, stripe_api, wallet)
from tickets.models import (CapacityPool, CheckIn, DoorStaff, HolderMessage, Order, Payout, PromoCode, RefundRequest,
                            Ticket, TicketType)

logger = logging.getLogger(__name__)


def tickets_view(manage=False, plan=True):
    def decorator(view):
        @login_required
        @wraps(view)
        def wrapper(request, event_id, *args, **kwargs):
            if not cached_access(request):
                raise Http404
            event = get_object_or_404(Event, pk=event_id)
            if not event.can_edit(request.user):
                raise Http404
            if plan and not inventory.plan_allows(event):
                return redirect('tickets_console:home', event_id=event.pk)
            if manage and not event.can_manage_team(request.user):
                messages.error(request, 'Only the owner and event admins can do that.')
                return redirect('tickets_console:home', event_id=event.pk)
            with timezone.override(_tz(event)):
                return view(request, event, *args, **kwargs)
        return wrapper
    return decorator


def _c(event, request, **extra):
    ts = inventory.ticketing(event)
    return _ctx(event, 'tickets', ts=ts, can_manage=event.can_manage_team(request.user), **extra)


def stats(event):
    paid = Order.objects.filter(event=event, status__in=[Order.PAID, Order.REFUNDED])
    totals = paid.aggregate(gross=Sum('total_cents'), fees=Sum('fee_cents'), refunded=Sum('refunded_cents'),
                            sub=Sum('subtotal_cents'), discount=Sum('discount_cents'))
    net = (totals['sub'] or 0) - (totals['discount'] or 0) - (totals['refunded'] or 0)
    reserved = inventory.reserved_by_type(event)
    left = inventory.availability(event, reserved=reserved)
    per_type = []
    sold_q = Ticket.objects.filter(event=event, status=Ticket.VALID)
    sold = dict(sold_q.values('ticket_type_id').annotate(n=Count('id')).values_list('ticket_type_id', 'n'))
    for t in TicketType.objects.filter(event=event).order_by('sort_order', 'id'):
        per_type.append({'type': t, 'sold': sold.get(t.pk, 0), 'held': reserved.get(t.pk, 0) - sold.get(t.pk, 0),
                         'left': left.get(t.pk), 'all_in': money.fmt(money.all_in(t.price_cents)),
                         'price': money.fmt(t.price_cents)})
    return {'orders': paid.filter(status=Order.PAID).count(), 'tickets': sold_q.count(),
            'net': money.fmt(net), 'net_cents': net, 'fees': money.fmt(totals['fees'] or 0),
            'refunded': money.fmt(totals['refunded'] or 0), 'per_type': per_type,
            'checked_in': sold_q.filter(checked_in_at__isnull=False).count(),
            'pending_requests': RefundRequest.objects.filter(order__event=event, status=RefundRequest.PENDING).count()}


@tickets_view(plan=False)
def home(request, event):
    ts = inventory.ticketing(event)
    if request.method == 'POST':
        action = request.POST.get('action')
        if not event.can_manage_team(request.user):
            messages.error(request, 'Only the owner and event admins can change ticketing.')
        elif action == 'enable_plan' and can_switch_plan(event, request.user):
            from studio import publishing
            publishing.switch_plan(event, Event.PLAN_STUDIO)
            messages.success(request, 'This event is on Studio + tickets.')
        elif action in ('enable', 'disable') and inventory.plan_allows(event):
            ts.enabled = action == 'enable'
            if not ts.enabled:
                ts.on_sale = False
            ts.save()
            event.bump_version()
        elif action in ('open', 'pause') and inventory.plan_allows(event) and ts.enabled:
            if action == 'open' and not TicketType.objects.filter(event=event, active=True).exists():
                messages.error(request, 'Add a ticket type first.')
            else:
                ts.on_sale = action == 'open'
                ts.save()
                event.bump_version()
                messages.success(request, 'Tickets are on sale.' if ts.on_sale else 'Sales paused.')
        return redirect('tickets_console:home', event_id=event.pk)
    if not inventory.plan_allows(event):
        return render(request, 'tickets/console/plan.html', _c(event, request,
                                                               can_switch=can_switch_plan(event, request.user)))
    return render(request, 'tickets/console/home.html', _c(
        event, request, stats=stats(event), state=inventory.sales_state(event, ts),
        paid_ready=inventory.paid_ready(event, ts), stripe_configured=stripe_api.configured(),
        page_url=settings.SITE_URL + f'/t/{event.event_code}/', apple=wallet.apple_enabled(),
        google=wallet.google_enabled(), draft_form=forms.DraftForm(),
        recent=Order.objects.filter(event=event, status=Order.PAID)[:8]))


# ---------------------------------------------------------------------------
# Ticket types, pools, promo codes, settings
# ---------------------------------------------------------------------------

@tickets_view()
def types(request, event):
    pool_form = forms.PoolForm(request.POST or None, prefix='pool')
    if request.method == 'POST' and event.can_manage_team(request.user):
        if request.POST.get('action') == 'pool' and pool_form.is_valid():
            pool = pool_form.save(commit=False)
            pool.event = event
            pool.save()
            messages.success(request, f'Added the shared capacity “{pool.name}”.')
        elif request.POST.get('action') == 'delete_pool':
            CapacityPool.objects.filter(event=event, pk=request.POST.get('pool')).delete()
        return redirect('tickets_console:types', event_id=event.pk)
    return render(request, 'tickets/console/types.html', _c(
        event, request, stats=stats(event), pools=CapacityPool.objects.filter(event=event), pool_form=pool_form))


@tickets_view(manage=True)
def type_edit(request, event, type_id=None):
    t = get_object_or_404(TicketType, pk=type_id, event=event) if type_id else TicketType(event=event)
    sold = inventory.reserved_by_type(event).get(t.pk, 0) if t.pk else 0
    form = forms.TicketTypeForm(request.POST or None, instance=t, event=event, sold=sold)
    if request.method == 'POST':
        if request.POST.get('action') == 'remove' and t.pk:
            if sold or t.tickets.exists():
                TicketType.objects.filter(pk=t.pk).update(active=False)
                messages.info(request, f'{t.name} is no longer sold (its tickets stay valid).')
            else:
                t.delete()
                messages.info(request, 'Removed.')
            event.bump_version()
            return redirect('tickets_console:types', event_id=event.pk)
        if form.is_valid():
            obj = form.save(commit=False)
            if t.pk:
                mark_locked(obj, form.changed_data)
            obj.active = True
            obj.save()
            event.bump_version()
            inventory.refresh_sold_out(event)
            messages.success(request, f'Saved {obj.name}.')
            return redirect('tickets_console:types', event_id=event.pk)
    return render(request, 'tickets/console/type_edit.html', _c(event, request, form=form, ticket_type=t, sold=sold))


@tickets_view()
def promos(request, event):
    form = forms.PromoForm(request.POST or None, event=event)
    if request.method == 'POST' and event.can_manage_team(request.user):
        if request.POST.get('action') == 'toggle':
            promo = get_object_or_404(PromoCode, pk=request.POST.get('promo'), event=event)
            promo.active = not promo.active
            promo.save()
            return redirect('tickets_console:promos', event_id=event.pk)
        if form.is_valid():
            promo = form.save(commit=False)
            promo.event = event
            promo.save()
            form.save_m2m()
            messages.success(request, f'Promo code {promo.code} created.')
            return redirect('tickets_console:promos', event_id=event.pk)
    return render(request, 'tickets/console/promos.html', _c(event, request, form=form,
                                                             promos=PromoCode.objects.filter(event=event)))


@tickets_view(manage=True)
def ticket_settings(request, event):
    ts = inventory.ticketing(event)
    form = forms.TicketSettingsForm(request.POST or None, instance=ts)
    if request.method == 'POST' and form.is_valid():
        obj = form.save(commit=False)
        locked = list(obj.locked_fields or [])
        locked += [f for f in form.changed_data if f in ts.CONTENT_FIELDS and f not in locked]
        obj.locked_fields = locked
        obj.updated_at = timezone.now()
        obj.save()
        payments.schedule_payout(event)
        messages.success(request, 'Saved.')
        return redirect('tickets_console:settings', event_id=event.pk)
    return render(request, 'tickets/console/settings.html', _c(event, request, form=form))


@tickets_view()
@require_POST
def draft(request, event):
    form = forms.DraftForm(request.POST)
    if not form.is_valid():
        messages.error(request, 'Tell the AI what tickets you want.')
        return redirect('tickets_console:home', event_id=event.pk)
    from tickets import ai
    job = ai.start(event, form.cleaned_data['instructions'], request.user)
    return redirect('console:job', job_id=job.pk)


# ---------------------------------------------------------------------------
# Orders, refunds, comps, attendees
# ---------------------------------------------------------------------------

@tickets_view()
def orders(request, event):
    qs = Order.objects.filter(event=event).exclude(status__in=[Order.HELD, Order.EXPIRED]).order_by('-created_at')
    q = request.GET.get('q', '').strip()
    if q:
        qs = qs.filter(Q(email__icontains=q) | Q(name__icontains=q) | Q(ref__iexact=q.replace('-', '')) |
                       Q(tickets__code__iexact=q.replace('-', '').upper())).distinct()
    status = request.GET.get('status', '')
    if status in dict(Order.STATUS_CHOICES):
        qs = qs.filter(status=status)
    requests_ = RefundRequest.objects.filter(order__event=event, status=RefundRequest.PENDING).select_related('order')
    return render(request, 'tickets/console/orders.html', _c(event, request, orders=qs[:200], q=q, status=status,
                                                             refund_requests=requests_,
                                                             comp_form=forms.CompForm(event=event)))


@tickets_view()
def order_detail(request, event, order_id):
    order = get_object_or_404(Order, pk=order_id, event=event)
    if request.method == 'POST':
        if not event.can_manage_team(request.user):
            messages.error(request, 'Only the owner and event admins can change orders.')
            return redirect('tickets_console:order', event_id=event.pk, order_id=order.pk)
        action = request.POST.get('action')
        try:
            if action == 'refund_all':
                payments.refund(order, reason=request.POST.get('reason', 'Refunded by the organizer'), by=request.user)
                messages.success(request, 'Refunded.')
            elif action == 'refund_tickets':
                chosen = list(order.tickets.filter(pk__in=request.POST.getlist('ticket'), status=Ticket.VALID))
                payments.refund(order, tickets=chosen, reason='Refunded by the organizer', by=request.user)
                messages.success(request, f'Refunded {len(chosen)} ticket(s).')
            elif action == 'refund_amount':
                amount = money.parse_dollars(request.POST.get('amount'))
                payments.refund(order, amount_cents=amount, reason='Partial refund', by=request.user)
                messages.success(request, f'Refunded {money.fmt(amount)}.')
            elif action == 'void':
                for t in order.tickets.filter(pk__in=request.POST.getlist('ticket'), status=Ticket.VALID):
                    payments.void(t, Ticket.VOID, 'void')
                messages.success(request, 'Those tickets no longer work.')
            elif action == 'resend':
                holders.resend(order)
                messages.success(request, f'Sent to {order.email}.')
            elif action in ('approve', 'decline'):
                req = get_object_or_404(RefundRequest, pk=request.POST.get('request'), order=order,
                                        status=RefundRequest.PENDING)
                if action == 'approve':
                    tickets = list(req.tickets.all()) or None
                    payments.refund(order, tickets=tickets, reason='Refund request approved', by=request.user)
                req.status = RefundRequest.APPROVED if action == 'approve' else RefundRequest.DECLINED
                req.decided_at, req.decided_by = timezone.now(), request.user
                req.save()
                messages.success(request, 'Refund approved.' if action == 'approve' else 'Request declined.')
            elif action == 'note':
                Order.objects.filter(pk=order.pk).update(note=request.POST.get('note', '')[:2000])
        except (payments.PaymentError, holders.HolderError, ValueError) as exc:
            messages.error(request, str(exc))
        return redirect('tickets_console:order', event_id=event.pk, order_id=order.pk)
    return render(request, 'tickets/console/order.html', _c(
        event, request, order=order, tickets=order.tickets.select_related('ticket_type'),
        items=order.items.select_related('ticket_type'), refunds=order.refunds.all(),
        requests_=order.refund_requests.all(), checkins=CheckIn.objects.filter(ticket__order=order)))


@tickets_view(manage=True)
@require_POST
def comp(request, event):
    form = forms.CompForm(request.POST, event=event)
    if form.is_valid():
        try:
            order = holders.comp(event, form.cleaned_data['ticket_type'], form.cleaned_data['quantity'],
                                 form.cleaned_data['email'], form.cleaned_data['name'], by=request.user)
            messages.success(request, f'Sent {form.cleaned_data["quantity"]} free ticket(s) to {order.email}.')
            return redirect('tickets_console:order', event_id=event.pk, order_id=order.pk)
        except holders.HolderError as exc:
            messages.error(request, str(exc))
    else:
        messages.error(request, 'Check the comp form.')
    return redirect('tickets_console:orders', event_id=event.pk)


@tickets_view()
def attendees_csv(request, event):
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="{event.event_code}-attendees.csv"'
    questions = [q for q in inventory.ticketing(event).questions or [] if isinstance(q, dict) and q.get('id')]
    writer = csv.writer(response)
    writer.writerow(['Ticket', 'Type', 'Status', 'Holder', 'Holder email', 'Order', 'Buyer', 'Buyer email', 'Paid',
                     'Checked in', 'Gate', *[q['label'] for q in questions]])
    for t in Ticket.objects.filter(event=event).select_related('ticket_type', 'order').order_by('id'):
        order_answers = (t.order.answers or {})
        writer.writerow([_safe(v) for v in [
            t.display_code, t.ticket_type.name, t.status, t.holder_name, t.holder_email, t.order.ref, t.order.name,
            t.order.email, money.fmt(t.price_cents + t.fee_cents),
            timezone.localtime(t.checked_in_at).strftime('%Y-%m-%d %H:%M') if t.checked_in_at else '',
            t.checked_in_gate, *[(t.answers or {}).get(q['id']) or order_answers.get(q['id']) or '' for q in questions]]])
    return response


def _safe(value):
    """No spreadsheet formulas from buyer-typed text (CSV injection)."""
    text = str(value if value is not None else '')
    return "'" + text if text[:1] in ('=', '+', '-', '@', '\t', '\r') else text


@tickets_view()
def checkins(request, event):
    data = checkin.counts(event)
    recent = CheckIn.objects.filter(event=event).select_related('ticket', 'ticket__ticket_type').order_by('-at')[:100]
    return render(request, 'tickets/console/checkins.html', _c(event, request, counts=data, recent=recent,
                                                               flagged=CheckIn.objects.filter(event=event, flagged=True)
                                                               .select_related('ticket')[:100]))


@tickets_view()
def door(request, event):
    form = forms.DoorStaffForm(request.POST or None)
    if request.method == 'POST':
        if not event.can_manage_team(request.user):
            messages.error(request, 'Only the owner and event admins can change the door team.')
        elif request.POST.get('action') == 'remove':
            DoorStaff.objects.filter(event=event, pk=request.POST.get('staff')).delete()
            messages.info(request, 'Removed. Their app loses access at its next sync.')
        elif form.is_valid():
            staff, created = DoorStaff.objects.get_or_create(event=event, email=form.cleaned_data['email'].lower(),
                                                             defaults={'name': form.cleaned_data['name'],
                                                                       'invited_by': request.user})
            emails.send_door_invite(staff)
            messages.success(request, f'{staff.email} can scan tickets in the Attnd app (Options → Door mode).')
        return redirect('tickets_console:door', event_id=event.pk)
    return render(request, 'tickets/console/door.html', _c(event, request, form=form,
                                                           staff=DoorStaff.objects.filter(event=event)))


@tickets_view(manage=True)
def message(request, event):
    form = forms.MessageForm(request.POST or None)
    recipients = sorted(set(Ticket.objects.filter(event=event, status=Ticket.VALID).values_list('holder_email', flat=True))
                        - {''})
    if request.method == 'POST' and form.is_valid():
        for email in recipients:
            emails.send_holder_message(event, email, form.cleaned_data['subject'], form.cleaned_data['body'])
        HolderMessage.objects.create(event=event, subject=form.cleaned_data['subject'], body=form.cleaned_data['body'],
                                     recipients=len(recipients), sent_by=request.user)
        messages.success(request, f'Emailed {len(recipients)} ticket holder(s).')
        return redirect('tickets_console:message', event_id=event.pk)
    return render(request, 'tickets/console/message.html', _c(event, request, form=form, count=len(recipients),
                                                              sent=HolderMessage.objects.filter(event=event)
                                                              .order_by('-sent_at')[:20]))


# ---------------------------------------------------------------------------
# Payouts (Stripe Connect Express) and cancelling
# ---------------------------------------------------------------------------

@tickets_view()
def payouts(request, event):
    ts = inventory.ticketing(event)
    account = ts.payout_account
    if request.GET.get('connect') and account is not None and stripe_api.configured():
        try:
            payments.sync_account(account)
        except stripe_api.StripeError:
            logger.exception('Could not refresh %s', account)
    if request.method == 'POST':
        if not event.can_manage_team(request.user):
            messages.error(request, 'Only the owner and event admins can set up payouts.')
            return redirect('tickets_console:payouts', event_id=event.pk)
        if not stripe_api.configured():
            messages.error(request, 'Stripe isn’t set up on this server yet.')
            return redirect('tickets_console:payouts', event_id=event.pk)
        action = request.POST.get('action')
        try:
            if action == 'connect':
                account = account or payments.connect_account(request.user, request.user.email)
                if ts.payout_account_id != account.pk:
                    ts.payout_account = account
                    ts.save(update_fields=['payout_account'])
                return HttpResponseRedirect(payments.onboarding_url(account, event))
            if action == 'dashboard' and account is not None:
                return HttpResponseRedirect(payments.dashboard_url(account))
        except stripe_api.StripeError as exc:
            messages.error(request, f'Stripe: {exc}')
        return redirect('tickets_console:payouts', event_id=event.pk)
    return render(request, 'tickets/console/payouts.html', _c(
        event, request, account=account, stripe_configured=stripe_api.configured(),
        test_mode=stripe_api.test_mode(), payouts=Payout.objects.filter(event=event),
        release_at=payments.release_at(event, ts), net=money.fmt(payments.event_net_cents(event))))


@tickets_view(manage=True)
def cancel(request, event):
    ts = inventory.ticketing(event)
    if request.method == 'POST':
        if request.POST.get('confirm', '').strip().lower() != event.event_code.lower():
            messages.error(request, f'Type the event code ({event.event_code}) to confirm.')
        else:
            done, failed = payments.cancel_event(event, request.POST.get('reason', ''), by=request.user)
            messages.success(request, f'Event cancelled. {done} order(s) refunded'
                             + (f', {failed} failed (we retry every 10 minutes).' if failed else '.'))
            return redirect('tickets_console:home', event_id=event.pk)
    paid = Order.objects.filter(event=event, status=Order.PAID)
    return render(request, 'tickets/console/cancel.html', _c(event, request, paid_count=paid.count(),
                                                             total=money.fmt(sum(o.total_cents - o.refunded_cents -
                                                                                 o.fee_refunded_cents for o in paid))))
