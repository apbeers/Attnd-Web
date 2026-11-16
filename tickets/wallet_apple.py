"""Apple Wallet event-ticket passes (.pkpass), signed server-side with the Pass Type ID certificate (PKCS #7).

A .pkpass is a zip of pass.json, images, manifest.json (SHA-1 of every file) and `signature` (a detached DER PKCS #7
signature of manifest.json by the pass certificate, with Apple's WWDR G4 intermediate included). Passes carry a
webServiceURL + authenticationToken, so the iPhone registers for updates (tickets.views_wallet) and we push when a
ticket changes (transfer, refund, event time or place changes): APNs with the pass certificate, topic = the Pass Type
ID, empty payload; the device then fetches the new pass.
"""

import hashlib
import io
import json
import logging
import ssl
import tempfile
import zipfile
from functools import lru_cache
from zoneinfo import ZoneInfo

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.serialization import pkcs7
from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)


def enabled():
    return bool(settings.APPLE_WALLET_CERT_PEM and settings.APPLE_WALLET_KEY_PEM and settings.APPLE_WALLET_WWDR_PEM
                and settings.APPLE_WALLET_PASS_TYPE_ID and settings.APPLE_WALLET_TEAM_ID)


def _credentials():
    cert = x509.load_pem_x509_certificate(settings.APPLE_WALLET_CERT_PEM.encode())
    key = serialization.load_pem_private_key(settings.APPLE_WALLET_KEY_PEM.encode(),
                                             password=settings.APPLE_WALLET_KEY_PASSWORD.encode() or None)
    wwdr = x509.load_pem_x509_certificate(settings.APPLE_WALLET_WWDR_PEM.encode())
    return cert, key, wwdr


def sign_manifest(manifest_bytes):
    cert, key, wwdr = _credentials()
    return (pkcs7.PKCS7SignatureBuilder().set_data(manifest_bytes).add_signer(cert, key, hashes.SHA256())
            .add_certificate(wwdr).sign(serialization.Encoding.DER,
                                        [pkcs7.PKCS7Options.DetachedSignature, pkcs7.PKCS7Options.Binary]))


@lru_cache(maxsize=8)
def _image(width, height, wordmark=False):
    from PIL import Image, ImageDraw, ImageFont
    img = Image.new('RGBA', (width, height), (0, 0, 0, 0) if wordmark else (67, 31, 134, 255))
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.load_default(size=int(height * (0.6 if wordmark else 0.7)))
    except TypeError:
        font = ImageFont.load_default()
    text = 'attnd.' if wordmark else 'a.'
    box = draw.textbbox((0, 0), text, font=font)
    x = 0 if wordmark else (width - (box[2] - box[0])) // 2
    draw.text((x, (height - (box[3] - box[1])) // 2 - box[1]), text, fill=(255, 255, 255, 255), font=font)
    out = io.BytesIO()
    img.save(out, 'PNG')
    return out.getvalue()


def _hex_to_rgb(value, fallback='rgb(67, 31, 134)'):
    value = (value or '').lstrip('#')
    if len(value) != 6:
        return fallback
    try:
        return f'rgb({int(value[0:2], 16)}, {int(value[2:4], 16)}, {int(value[4:6], 16)})'
    except ValueError:
        return fallback


def pass_json(ticket, wallet_pass, token):
    event = ticket.event
    try:
        tz = ZoneInfo(event.timezone or 'UTC')
    except Exception:
        tz = ZoneInfo('UTC')
    start = timezone.localtime(event.start_time, tz)
    voided = ticket.status != ticket.VALID or wallet_pass.voided
    data = {
        'formatVersion': 1,
        'passTypeIdentifier': settings.APPLE_WALLET_PASS_TYPE_ID,
        'serialNumber': wallet_pass.serial,
        'teamIdentifier': settings.APPLE_WALLET_TEAM_ID,
        'organizationName': 'Attnd',
        'description': f'Ticket for {event.name}',
        'webServiceURL': settings.SITE_URL + '/wallet/',
        'authenticationToken': wallet_pass.auth_token,
        'relevantDate': event.start_time.isoformat(),
        'expirationDate': event.effective_end.isoformat(),
        'voided': voided,
        'backgroundColor': _hex_to_rgb(event.accent_color),
        'foregroundColor': 'rgb(255, 255, 255)',
        'labelColor': 'rgb(230, 222, 250)',
        'logoText': event.name[:40],
        'barcodes': [{'format': 'PKBarcodeFormatQR', 'message': token if not wallet_pass.voided else 'VOID', 'messageEncoding': 'iso-8859-1',
                      'altText': ticket.display_code}],
        'eventTicket': {
            'headerFields': [{'key': 'date', 'label': start.strftime('%a %b %-d'), 'value': start.strftime('%-I:%M %p')}],
            'primaryFields': [{'key': 'event', 'label': 'EVENT', 'value': event.name}],
            'secondaryFields': [{'key': 'type', 'label': 'TICKET', 'value': ticket.ticket_type.name},
                                {'key': 'holder', 'label': 'NAME', 'value': ticket.holder_name or ' '}],
            'auxiliaryFields': [{'key': 'code', 'label': 'CODE', 'value': ticket.display_code},
                                {'key': 'order', 'label': 'ORDER', 'value': ticket.order.ref}],
            'backFields': [
                {'key': 'where', 'label': 'Where', 'value': event.address or event.name},
                {'key': 'status', 'label': 'Status', 'value': 'Valid' if not voided else 'No longer valid'},
                {'key': 'manage', 'label': 'Manage your ticket',
                 'value': settings.SITE_URL + f'/tickets/ticket/{ticket.public_id}/{ticket.key}/'},
            ],
        },
    }
    if settings.TICKETS_DEV_TOOLS:
        data['eventTicket']['headerFields'].insert(0, {'key': 'dev', 'label': 'TEST', 'value': 'DEV'})
    if event.latitude is not None and event.longitude is not None:
        data['locations'] = [{'latitude': float(event.latitude), 'longitude': float(event.longitude),
                              'relevantText': f'Your ticket for {event.name}'}]
    return data


def build(ticket, wallet_pass, token):
    """The signed .pkpass bytes."""
    files = {
        'pass.json': json.dumps(pass_json(ticket, wallet_pass, token), ensure_ascii=False).encode(),
        'icon.png': _image(29, 29), 'icon@2x.png': _image(58, 58), 'icon@3x.png': _image(87, 87),
        'logo.png': _image(160, 50, True), 'logo@2x.png': _image(320, 100, True),
    }
    manifest = json.dumps({name: hashlib.sha1(data).hexdigest() for name, data in files.items()},
                          sort_keys=True).encode()
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as zf:
        for name, data in files.items():
            zf.writestr(name, data)
        zf.writestr('manifest.json', manifest)
        zf.writestr('signature', sign_manifest(manifest))
    return out.getvalue()


def _ssl_context():
    context = ssl.create_default_context()
    with tempfile.NamedTemporaryFile('w', suffix='.pem') as cert_file, \
            tempfile.NamedTemporaryFile('w', suffix='.pem') as key_file:
        cert_file.write(settings.APPLE_WALLET_CERT_PEM)
        cert_file.flush()
        key_file.write(settings.APPLE_WALLET_KEY_PEM)
        key_file.flush()
        context.load_cert_chain(cert_file.name, key_file.name, password=settings.APPLE_WALLET_KEY_PASSWORD or None)
    return context


def push(push_tokens):
    """Tell devices a pass changed (they fetch it from the web service). Returns the tokens Apple says are gone."""
    if not enabled() or not push_tokens:
        return []
    import httpx
    gone = []
    try:
        with httpx.Client(http2=True, timeout=10, verify=_ssl_context()) as client:
            for token in push_tokens:
                try:
                    resp = client.post(f'https://api.push.apple.com/3/device/{token}', content=b'{}',
                                       headers={'apns-topic': settings.APPLE_WALLET_PASS_TYPE_ID})
                except httpx.HTTPError as exc:
                    logger.warning('Wallet push failed: %s', exc)
                    continue
                if resp.status_code == 410:
                    gone.append(token)
    except Exception:
        logger.exception('Wallet push could not start')
    return gone
