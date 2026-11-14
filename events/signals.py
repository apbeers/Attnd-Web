"""Bump Event.content_version whenever attendee-visible content changes."""

from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from events.content import maybe_bump
from events.models import Event, Location, Notification, ScheduledItem

# Event fields that do not affect what attendees see
_EVENT_BOOKKEEPING = {'content_version', 'content_updated_at', 'last_refreshed_at',
                      'locked_fields', 'source_urls', 'source_text', 'refresh_schedule',
                      'auto_accept_minor', 'order_id'}


@receiver(post_save, sender=Event)
def _event_saved(sender, instance, created, update_fields=None, **kwargs):
    if created:
        return
    if update_fields and set(update_fields) <= _EVENT_BOOKKEEPING:
        return
    maybe_bump(instance.pk)


@receiver([post_save, post_delete], sender=Location)
@receiver([post_save, post_delete], sender=ScheduledItem)
def _content_changed(sender, instance, **kwargs):
    maybe_bump(instance.event_id)


@receiver(post_save, sender=Notification)
def _notification_saved(sender, instance, update_fields=None, **kwargs):
    if instance.sent:
        maybe_bump(instance.event_id)
