"""QR tokens: compact Ed25519-signed tickets that a door device verifies OFFLINE (spec: Tickets, Wallet, scanning).

Token (ASCII, goes into the QR code, Apple/Google Wallet barcodes and the apps' "My tickets"):

    ATK1.<payload>.<signature>

- `payload` = base64url without padding of compact JSON with sorted keys, e.g. {"e":12,"k":1,"n":1,"t":345,"y":7}
  (e = event id, t = ticket id, y = ticket type id, n = ticket version, k = key id).
- `signature` = base64url without padding of the 64-byte Ed25519 signature over the ASCII bytes "ATK1.<payload>".
- Each event has its own key pair (TicketKey); doors download the public keys (raw 32 bytes, base64url) with the
  ticket list. A token is good iff the signature verifies with the event's key `k`, `e` is the event, and the ticket
  is not revoked at version `n` (Revocation: version <= n revoked) nor refunded/void in the downloaded list.

Private keys are stored encrypted with a key derived from settings.TICKETS_KEY_SECRET (Fernet, AES-128-CBC + HMAC).
"""

import base64
import hashlib
import json

from cryptography.exceptions import InvalidSignature
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from django.conf import settings
from django.db import transaction

PREFIX = 'ATK1'


def b64url(data):
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode()


def unb64url(text):
    text = text.strip()
    return base64.urlsafe_b64decode(text + '=' * (-len(text) % 4))


def _fernet():
    digest = hashlib.sha256(('attnd-ticket-keys:' + settings.TICKETS_KEY_SECRET).encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def _raw_public(private):
    return private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def event_key(event):
    """The event's active signing key, created on first use."""
    from tickets.models import TicketKey
    key = TicketKey.objects.filter(event=event, active=True).order_by('-kid').first()
    if key is not None:
        return key
    with transaction.atomic():
        existing = TicketKey.objects.select_for_update().filter(event=event).order_by('-kid')
        latest = existing.first()
        if latest is not None and latest.active:
            return latest
        private = Ed25519PrivateKey.generate()
        raw = private.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                                    serialization.NoEncryption())
        return TicketKey.objects.create(
            event=event, kid=(latest.kid + 1) if latest else 1, public_key=b64url(_raw_public(private)),
            private_key=_fernet().encrypt(raw).decode())


def _private(key):
    return Ed25519PrivateKey.from_private_bytes(_fernet().decrypt(key.private_key.encode()))


def payload_for(ticket, key):
    return {'e': ticket.event_id, 'k': key.kid, 'n': ticket.version, 't': ticket.id, 'y': ticket.ticket_type_id}


def token_for(ticket):
    """The current QR token of a ticket."""
    key = event_key(ticket.event)
    body = b64url(json.dumps(payload_for(ticket, key), separators=(',', ':'), sort_keys=True).encode())
    message = f'{PREFIX}.{body}'
    return f'{message}.{b64url(_private(key).sign(message.encode()))}'


def public_keys(event):
    from tickets.models import TicketKey
    event_key(event)
    return [{'kid': k.kid, 'alg': 'Ed25519', 'public_key': k.public_key, 'active': k.active}
            for k in TicketKey.objects.filter(event=event).order_by('kid')]


def parse(token):
    """(payload dict, signed message bytes, signature bytes) or None if it isn't an ATK1 token."""
    parts = (token or '').strip().split('.')
    if len(parts) != 3 or parts[0] != PREFIX:
        return None
    try:
        payload = json.loads(unb64url(parts[1]))
        signature = unb64url(parts[2])
    except (ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or not all(isinstance(payload.get(k), int) for k in ('e', 'k', 'n', 't')):
        return None
    return payload, f'{parts[0]}.{parts[1]}'.encode(), signature


def verify(token, public_keys_by_kid):
    """The payload if the signature is valid for one of `public_keys_by_kid` ({kid: base64url raw key}), else None.

    Exactly what a door device does offline (no database)."""
    parsed = parse(token)
    if parsed is None:
        return None
    payload, message, signature = parsed
    raw = public_keys_by_kid.get(payload['k'])
    if raw is None:
        return None
    try:
        Ed25519PublicKey.from_public_bytes(unb64url(raw)).verify(signature, message)
    except (InvalidSignature, ValueError):
        return None
    return payload


def verify_for_event(token, event):
    from tickets.models import TicketKey
    keys = {k.kid: k.public_key for k in TicketKey.objects.filter(event=event)}
    payload = verify(token, keys)
    if payload is None or payload['e'] != event.id:
        return None
    return payload
