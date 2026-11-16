"""Ticket housekeeping, run by `manage.py run_scheduled_tasks` (Heroku Scheduler, every 10 minutes)."""

import logging
from datetime import timedelta

from django.utils import timezone

logger = logging.getLogger(__name__)


def run():
    from tickets import inventory, payments
    from tickets.models import AccessToken, VerificationCode
    out = {}
    steps = [
        ('holds expired', inventory.expire_holds),
        ('cancellation refunds', lambda: payments.process_cancellations()[0]),
        ('payouts released', payments.release_due),
        ('old codes deleted', lambda: VerificationCode.objects.filter(
            expires_at__lt=timezone.now() - timedelta(days=1)).delete()[0]),
        ('expired tokens deleted', lambda: AccessToken.objects.filter(
            expires_at__lt=timezone.now() - timedelta(days=30)).delete()[0]),
    ]
    for name, step in steps:
        try:
            out[name] = step()
        except Exception:  # never stop the other housekeeping
            logger.exception('Tickets task "%s" failed', name)
    return out
