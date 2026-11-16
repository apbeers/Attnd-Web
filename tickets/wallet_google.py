"""Google Wallet event tickets: one Event Ticket class per event, one object per ticket, "Save to Google Wallet" links.

The save link carries the class and object inline in a JWT signed (RS256) with the issuer's service account key, so
nothing is created through the API until someone saves a ticket. Changes (transfer, refund, void, event time) are
PATCHed to the object with the Wallet REST API. Dev uses its own class prefix (GOOGLE_WALLET_CLASS_PREFIX).
"""

import json
import logging
import time
from zoneinfo import ZoneInfo

import jwt
from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)

SAVE_URL = 'https://pay.google.com/gp/v/save/'
API = 'https://walletobjects.googleapis.com/walletobjects/v1'
SCOPE = 'https://www.googleapis.com/auth/wallet_object.issuer'


def enabled():
    return bool(settings.GOOGLE_WALLET_ISSUER_ID and settings.GOOGLE_WALLET_SERVICE_ACCOUNT_JSON)


def _account():
    return json.loads(settings.GOOGLE_WALLET_SERVICE_ACCOUNT_JSON)


def class_id(event):
    return f'{settings.GOOGLE_WALLET_ISSUER_ID}.{settings.GOOGLE_WALLET_CLASS_PREFIX}-event-{event.pk}'


def object_id(wallet_pass):
    return f'{settings.GOOGLE_WALLET_ISSUER_ID}.{settings.GOOGLE_WALLET_CLASS_PREFIX}-ticket-{wallet_pass.serial}'


def _text(value):
    return {'defaultValue': {'language': 'en-US', 'value': value}}


def event_class(event):
    data = {
        'id': class_id(event),
        'issuerName': 'Attnd',
        'reviewStatus': 'UNDER_REVIEW',
        'eventName': _text(event.name + (' (DEV)' if settings.TICKETS_DEV_TOOLS else '')),
        'dateTime': {'start': event.start_time.isoformat(), 'end': event.effective_end.isoformat()},
        'hexBackgroundColor': event.accent_color or '#431f86',
        'logo': {'sourceUri': {'uri': settings.SITE_URL + '/static/home/img/favicon.png'},
                 'contentDescription': _text('Attnd')},
    }
    if event.address:
        data['venue'] = {'name': _text(event.address.split(',')[0][:100]), 'address': _text(event.address)}
    return data


def ticket_object(ticket, wallet_pass, token):
    event = ticket.event
    try:
        tz = ZoneInfo(event.timezone or 'UTC')
    except Exception:
        tz = ZoneInfo('UTC')
    data = {
        'id': object_id(wallet_pass),
        'classId': class_id(event),
        'state': 'ACTIVE' if ticket.status == ticket.VALID and not wallet_pass.voided else 'INACTIVE',
        'barcode': {'type': 'QR_CODE', 'value': token, 'alternateText': ticket.display_code},
        'ticketHolderName': ticket.holder_name or '',
        'ticketNumber': ticket.display_code,
        'ticketType': _text(ticket.ticket_type.name),
        'hexBackgroundColor': event.accent_color or '#431f86',
        'textModulesData': [{'header': 'When', 'body': timezone.localtime(event.start_time, tz).strftime(
            '%A, %B %-d, %-I:%M %p'), 'id': 'when'}],
        'linksModuleData': {'uris': [{'uri': settings.SITE_URL + f'/tickets/ticket/{ticket.public_id}/{ticket.key}/',
                                      'description': 'Manage your ticket', 'id': 'manage'}]},
    }
    if event.latitude is not None and event.longitude is not None:
        data['locations'] = [{'latitude': float(event.latitude), 'longitude': float(event.longitude)}]
    return data


def save_url(ticket, wallet_pass, token):
    account = _account()
    claims = {
        'iss': account['client_email'], 'aud': 'google', 'typ': 'savetowallet', 'iat': int(time.time()),
        'origins': [settings.SITE_URL],
        'payload': {'eventTicketClasses': [event_class(ticket.event)],
                    'eventTicketObjects': [ticket_object(ticket, wallet_pass, token)]},
    }
    signed = jwt.encode(claims, account['private_key'], algorithm='RS256',
                        headers={'kid': account.get('private_key_id', '')} if account.get('private_key_id') else None)
    return SAVE_URL + signed


def _access_token():
    from google.auth.transport.requests import Request
    from google.oauth2 import service_account
    creds = service_account.Credentials.from_service_account_info(_account(), scopes=[SCOPE])
    creds.refresh(Request())
    return creds.token


def update(ticket, wallet_pass, token):
    """PATCH the saved object (no-op if nobody saved it: 404)."""
    if not enabled():
        return False
    import httpx
    try:
        headers = {'Authorization': f'Bearer {_access_token()}'}
        obj = ticket_object(ticket, wallet_pass, token)
        resp = httpx.patch(f'{API}/eventTicketObject/{obj["id"]}', json=obj, headers=headers, timeout=10)
        if resp.status_code == 404:
            return False
        resp.raise_for_status()
        return True
    except Exception:
        logger.exception('Google Wallet update failed for ticket %s', ticket.pk)
        return False
