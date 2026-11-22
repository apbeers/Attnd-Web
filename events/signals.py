"""Bump Event.content_version whenever attendee-visible content changes."""

from django.db.models.signals import post_delete, post_save, pre_save
from django.dispatch import receiver

from events.content import maybe_bump
from events.models import Event, Location, Notification, ScheduledItem

# Event fields that do not affect what attendees see
_EVENT_BOOKKEEPING = {'content_version', 'content_updated_at', 'last_refreshed_at',
                      'locked_fields', 'source_urls', 'source_text', 'refresh_schedule',
                      'update_mode', 'sources_checked_at', 'plan',
                      'theme_web', 'cover', 'cover_credit', 'cover_source', 'cover_checked_at',
                      'cover_cost_usd'}  # (theme_web: the website only; the apps' theme_apps does change the bundle)


@receiver(pre_save, sender=Event)
def _event_before(sender, instance, raw=False, **kwargs):
    """§4: remember what attendees see without a Publish (status, paid, listing), to tell after the save."""
    if raw or not instance.pk:
        return
    from events.drafts import BYPASS_EVENT_FIELDS
    instance._bypass_before = Event.objects.filter(pk=instance.pk).values(*BYPASS_EVENT_FIELDS).first()


@receiver(post_save, sender=Event)
def _event_saved(sender, instance, created, update_fields=None, **kwargs):
    if created:
        return
    if update_fields and set(update_fields) <= _EVENT_BOOKKEEPING:
        return
    maybe_bump(instance.pk)
    from events import drafts
    before = getattr(instance, '_bypass_before', None)
    if before and any(before.get(f) != getattr(instance, f) for f in drafts.BYPASS_EVENT_FIELDS):
        if instance.status != Event.STATUS_DRAFT and not drafts.published(instance):
            drafts.publish(instance, push=False)  # its first Publish (draft → published)
        else:
            drafts.sync(instance.pk)  # status, paid, listing: straight to attendees


@receiver([post_save, post_delete], sender=Location)
@receiver([post_save, post_delete], sender=ScheduledItem)
def _content_changed(sender, instance, **kwargs):
    maybe_bump(instance.event_id)


@receiver(post_save, sender=Notification)
def _notification_saved(sender, instance, update_fields=None, **kwargs):
    if instance.sent:
        maybe_bump(instance.event_id)
        from events import drafts
        drafts.sync(instance.event_id)  # §4: sent notifications never wait for Publish


@receiver(post_save, sender=Location)
def _location_image_link(sender, instance, raw=False, **kwargs):
    """An outside image link (an import, an organizer's URL) is copied into our storage (attnd/images.py)."""
    if raw or not instance.image_url or instance.image:
        return
    from attnd import images
    if not images.is_ours(instance.image_url):
        images.schedule_location(instance.pk)


@receiver(post_save, sender=Event)
def _event_logo_link(sender, instance, raw=False, update_fields=None, **kwargs):
    if raw or not instance.logo_url or (update_fields and 'logo_url' not in update_fields):
        return
    from attnd import images
    if not images.is_ours(instance.logo_url) and instance.logo_source_url != instance.logo_url:
        images.schedule_event_logo(instance.pk)


@receiver(post_save, sender=Location)
def _location_created(sender, instance, created, raw=False, **kwargs):
    """Stage D: a new place without an image gets one looked for in the background (events.place_images)."""
    if created and not raw:
        from events import place_images
        if place_images.wanted(instance):
            place_images.schedule(instance.pk)


@receiver(post_save, sender=ScheduledItem)
def _item_performers(sender, instance, raw=False, **kwargs):
    """API-030: new or renamed performers get their Apple Music link looked up in the background."""
    if raw:
        return
    from events import music
    if music.enabled() and music.wanted(instance):
        music.schedule(instance.pk)
