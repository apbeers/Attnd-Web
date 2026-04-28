"""
Management command: expire_trial_events

Deletes Event (and cascaded Locations/Notifications/Devices) owned by
trialuser that are older than TRIAL_EXPIRY_DAYS days.

Usage:
    python manage.py expire_trial_events

Set up as a Heroku Scheduler job running daily:
    python manage.py expire_trial_events
"""

from datetime import timedelta

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand
from django.utils import timezone

from events.models import Event

TRIAL_USERNAME = "trialuser"
TRIAL_EXPIRY_DAYS = 7


class Command(BaseCommand):
    help = f"Delete trial events older than {TRIAL_EXPIRY_DAYS} days"

    def handle(self, *args, **options):
        try:
            trial_user = User.objects.get(username=TRIAL_USERNAME)
        except User.DoesNotExist:
            self.stdout.write("trialuser does not exist — nothing to expire.")
            return

        cutoff = timezone.now() - timedelta(days=TRIAL_EXPIRY_DAYS)
        expired = Event.objects.filter(user=trial_user, created_at__lt=cutoff)
        count = expired.count()

        if count == 0:
            self.stdout.write("No expired trial events found.")
            return

        expired.delete()
        self.stdout.write(
            self.style.SUCCESS(f"Deleted {count} trial event(s) older than {TRIAL_EXPIRY_DAYS} days.")
        )
