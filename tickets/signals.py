"""Wallet passes show the event's name, time and place: when those change, saved passes are updated."""

from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver

from events.models import Event

WATCHED = ('name', 'start_time', 'end_time', 'address', 'latitude', 'longitude', 'accent_color')


@receiver(pre_save, sender=Event)
def _remember(sender, instance, **kwargs):
    if instance.pk:
        old = Event.objects.filter(pk=instance.pk).values(*WATCHED).first()
        instance._tickets_old = old


@receiver(post_save, sender=Event)
def _changed(sender, instance, created, **kwargs):
    old = getattr(instance, '_tickets_old', None)
    if created or not old:
        return
    if any(str(old[f]) != str(getattr(instance, f)) for f in WATCHED if f not in ('latitude', 'longitude')):
        from tickets.models import WalletPass
        if WalletPass.objects.filter(ticket__event=instance).exists():
            from django.db import transaction
            from tickets import wallet
            transaction.on_commit(lambda: wallet.event_changed(instance))
