"""Staff walkthrough kit for tickets (spec: Testing): the DEMO ticketed event, test cards, codes for test buyers, and a
"fast-forward to after the event" button that only exists on dev (TICKETS_DEV_TOOLS)."""

from datetime import timedelta

from django.conf import settings
from django.contrib import messages
from django.core.management import call_command
from django.shortcuts import redirect, render
from django.utils import timezone

from events.models import Event
from studio.access import staff_required
from tickets import payments, stripe_api
from tickets.models import Payout, VerificationCode

TEST_CARDS = [
    ('4242 4242 4242 4242', 'Succeeds'),
    ('4000 0000 0000 0002', 'Declined'),
    ('4000 0025 0000 3155', '3-D Secure authentication'),
    ('4000 0000 0000 0259', 'Succeeds, then disputed (fraudulent)'),
]


def fast_forward(event):
    """Move the event so it ended (and its payout hold passed), then release payouts. Dev only."""
    if not settings.TICKETS_DEV_TOOLS:
        raise PermissionError('Fast-forward is only on dev')
    length = event.effective_end - event.start_time
    end = timezone.now() - timedelta(days=settings.TICKETS_PAYOUT_DELAY_DAYS + 1)
    Event.objects.filter(pk=event.pk).update(start_time=end - length, end_time=end)
    event.refresh_from_db()
    for payout in Payout.objects.filter(event=event, status=Payout.SCHEDULED):
        payout.release_at = payments.release_at(event)
        payout.save(update_fields=['release_at'])
    payments.schedule_payout(event)
    return payments.release_due()


@staff_required
def demo(request):
    code = settings.TICKETS_DEMO_EVENT_CODE
    event = Event.objects.filter(event_code__iexact=code).first()
    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'load':
            call_command('load_ticket_demo', '--code', code, '--owner', request.user.email)
            messages.success(request, f'Loaded the {code} ticket demo.')
        elif action == 'fast_forward' and event is not None and settings.TICKETS_DEV_TOOLS:
            released = fast_forward(event)
            messages.success(request, f'The event is now over; {released} payout(s) released.')
        return redirect('tickets_console:demo')
    codes = []
    if settings.TICKETS_DEV_TOOLS:
        codes = VerificationCode.objects.exclude(test_code='').order_by('-created_at')[:10]
    return render(request, 'tickets/console/demo.html', {
        'event': None, 'demo_event': event, 'code': code, 'cards': TEST_CARDS, 'dev_tools': settings.TICKETS_DEV_TOOLS,
        'stripe_configured': stripe_api.configured(), 'test_mode': stripe_api.test_mode(), 'codes': codes,
        'test_domain': settings.TICKETS_TEST_EMAIL_DOMAIN, 'section': 'tickets_demo'})
