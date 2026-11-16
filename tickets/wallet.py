"""Wallet passes for tickets (Apple and Google): links, files and keeping them up to date."""

import logging
import uuid

from django.utils import timezone

from tickets import signing, wallet_apple, wallet_google
from tickets.models import Ticket, WalletPass, WalletRegistration

logger = logging.getLogger(__name__)


def apple_enabled():
    return wallet_apple.enabled()


def google_enabled():
    return wallet_google.enabled()


def pass_for(ticket, platform):
    current = WalletPass.objects.filter(ticket=ticket, platform=platform, voided=False).first()
    if current is not None:
        return current
    return WalletPass.objects.create(ticket=ticket, platform=platform, serial=uuid.uuid4().hex)


def apple_pkpass(ticket):
    wallet_pass = pass_for(ticket, WalletPass.APPLE)
    return wallet_apple.build(ticket, wallet_pass, signing.token_for(ticket)), wallet_pass


def apple_pkpass_for(wallet_pass):
    """The pass the web service hands back for a serial (a retired pass comes back voided)."""
    ticket = wallet_pass.ticket
    return wallet_apple.build(ticket, wallet_pass, signing.token_for(ticket))


def google_save_url(ticket):
    wallet_pass = pass_for(ticket, WalletPass.GOOGLE)
    return wallet_google.save_url(ticket, wallet_pass, signing.token_for(ticket))


def retire(ticket):
    """Before a transfer: the old holder's passes turn void (and are pushed); the new holder gets new ones."""
    WalletPass.objects.filter(ticket=ticket, voided=False).update(voided=True, updated_at=timezone.now())


def ticket_changed(ticket_pk):
    """A ticket's QR, holder or status changed: refresh its passes (best effort, never raises)."""
    try:
        ticket = Ticket.objects.select_related('event', 'ticket_type', 'order').get(pk=ticket_pk)
        passes = list(WalletPass.objects.filter(ticket=ticket))
        if not passes:
            return
        WalletPass.objects.filter(ticket=ticket).update(updated_at=timezone.now())
        token = signing.token_for(ticket)
        for wallet_pass in passes:
            if wallet_pass.platform == WalletPass.APPLE and apple_enabled():
                tokens = list(wallet_pass.registrations.values_list('push_token', flat=True))
                for gone in wallet_apple.push(tokens):
                    WalletRegistration.objects.filter(wallet_pass=wallet_pass, push_token=gone).delete()
            elif wallet_pass.platform == WalletPass.GOOGLE and google_enabled():
                wallet_google.update(ticket, wallet_pass, token)
    except Exception:
        logger.exception('Wallet update failed for ticket %s', ticket_pk)


def event_changed(event):
    """The event's time, name or place changed: every pass shows it."""
    for ticket_pk in WalletPass.objects.filter(ticket__event=event).values_list('ticket_id', flat=True).distinct():
        ticket_changed(ticket_pk)
