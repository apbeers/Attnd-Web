"""
Push delivery through APNs (iOS) and FCM HTTP v1 (Android).

Configuration (all optional; a platform with no credentials is skipped and logged):

    APNS_AUTH_KEY      contents of the .p8 key (literal "\\n" sequences are accepted)
    APNS_KEY_ID        key ID of that .p8 key
    APNS_TEAM_ID       defaults to APPLE_TEAM_ID
    APNS_TOPIC         app bundle ID, defaults to APPLE_APP_BUNDLE_ID
    APNS_USE_SANDBOX   truthy → api.sandbox.push.apple.com (Xcode / TestFlight dev builds)

    FCM_PROJECT_ID              Firebase project ID
    FCM_SERVICE_ACCOUNT_JSON    service account JSON; defaults to GOOGLE_SERVICE_ACCOUNT_JSON
"""

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import timedelta

import httpx
import jwt
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from events.models import Device, Event, Notification

logger = logging.getLogger(__name__)


@dataclass
class SendResult:
    delivered: int = 0
    failed: int = 0
    skipped: int = 0
    invalid_tokens: list = field(default_factory=list)


# ---------------------------------------------------------------------------
# APNs
# ---------------------------------------------------------------------------

_apns_jwt_cache = {'token': None, 'issued': 0}


def _apns_config():
    key = (getattr(settings, 'APNS_AUTH_KEY', '') or '').replace('\\n', '\n')
    key_id = getattr(settings, 'APNS_KEY_ID', '')
    team = getattr(settings, 'APNS_TEAM_ID', '') or getattr(settings, 'APPLE_TEAM_ID', '')
    topic = getattr(settings, 'APNS_TOPIC', '') or getattr(settings, 'APPLE_APP_BUNDLE_ID', '')
    if not (key and key_id and team and topic):
        return None
    host = ('https://api.sandbox.push.apple.com' if getattr(settings, 'APNS_USE_SANDBOX', False)
            else 'https://api.push.apple.com')
    return {'key': key, 'key_id': key_id, 'team': team, 'topic': topic, 'host': host}


def _apns_token(cfg):
    # Apple rejects provider tokens older than an hour and throttles refreshes faster than 20 min.
    now = time.time()
    if _apns_jwt_cache['token'] and now - _apns_jwt_cache['issued'] < 45 * 60:
        return _apns_jwt_cache['token']
    token = jwt.encode({'iss': cfg['team'], 'iat': int(now)}, cfg['key'], algorithm='ES256',
                       headers={'kid': cfg['key_id']})
    _apns_jwt_cache.update(token=token, issued=now)
    return token


def send_apns(tokens, payload, push_type='alert', priority='10'):
    result = SendResult()
    cfg = _apns_config()
    if cfg is None:
        result.skipped = len(tokens)
        if tokens:
            logger.warning('APNs not configured; skipped %d iOS devices', len(tokens))
        return result
    headers = {
        'authorization': f'bearer {_apns_token(cfg)}',
        'apns-topic': cfg['topic'],
        'apns-push-type': push_type,
        'apns-priority': priority,
    }
    body = json.dumps(payload)
    with httpx.Client(http2=True, timeout=10) as client:
        for token in tokens:
            try:
                resp = client.post(f"{cfg['host']}/3/device/{token}", content=body, headers=headers)
            except httpx.HTTPError as exc:
                logger.warning('APNs request failed: %s', exc)
                result.failed += 1
                continue
            if resp.status_code == 200:
                result.delivered += 1
            else:
                result.failed += 1
                reason = ''
                try:
                    reason = resp.json().get('reason', '')
                except ValueError:
                    pass
                if resp.status_code == 410 or reason in ('BadDeviceToken', 'Unregistered', 'DeviceTokenNotForTopic'):
                    result.invalid_tokens.append(token)
                else:
                    logger.warning('APNs %s for token …%s: %s', resp.status_code, token[-6:], reason)
    return result


# ---------------------------------------------------------------------------
# FCM
# ---------------------------------------------------------------------------

_fcm_creds = {'creds': None}


def _fcm_access_token():
    raw = getattr(settings, 'FCM_SERVICE_ACCOUNT_JSON', '') or getattr(settings, 'GOOGLE_SERVICE_ACCOUNT_JSON', '')
    project = getattr(settings, 'FCM_PROJECT_ID', '')
    if not (raw and project):
        return None, None
    from google.auth.transport.requests import Request
    from google.oauth2 import service_account

    creds = _fcm_creds['creds']
    if creds is None:
        creds = service_account.Credentials.from_service_account_info(
            json.loads(raw), scopes=['https://www.googleapis.com/auth/firebase.messaging'])
        _fcm_creds['creds'] = creds
    if not creds.valid:
        creds.refresh(Request())
    return creds.token, project


def send_fcm(tokens, data, notification=None):
    result = SendResult()
    try:
        access_token, project = _fcm_access_token()
    except Exception:
        logger.exception('Could not obtain FCM credentials')
        access_token, project = None, None
    if not access_token:
        result.skipped = len(tokens)
        if tokens:
            logger.warning('FCM not configured; skipped %d Android devices', len(tokens))
        return result
    url = f'https://fcm.googleapis.com/v1/projects/{project}/messages:send'
    headers = {'Authorization': f'Bearer {access_token}'}
    with httpx.Client(timeout=10) as client:
        for token in tokens:
            message = {'token': token, 'data': {k: str(v) for k, v in data.items()},
                       'android': {'priority': 'high'}}
            if notification:
                message['notification'] = notification
            try:
                resp = client.post(url, json={'message': message}, headers=headers)
            except httpx.HTTPError as exc:
                logger.warning('FCM request failed: %s', exc)
                result.failed += 1
                continue
            if resp.status_code == 200:
                result.delivered += 1
            else:
                result.failed += 1
                text = resp.text
                if resp.status_code == 404 or 'UNREGISTERED' in text or 'INVALID_ARGUMENT' in text:
                    result.invalid_tokens.append(token)
                else:
                    logger.warning('FCM %s: %s', resp.status_code, text[:200])
    return result


# ---------------------------------------------------------------------------
# High level
# ---------------------------------------------------------------------------

def _recipients(event):
    devices = Device.objects.filter(event=event, subscribed=True).exclude(push_token='')
    ios = [d.push_token for d in devices if d.platform == 'ios']
    android = [d.push_token for d in devices if d.platform == 'android']
    return ios, android


def _forget(tokens):
    if tokens:
        Device.objects.filter(push_token__in=tokens).update(push_token='')


def deliver(event, alert=None, data=None, silent=False):
    """Send one message to every subscribed device in the event. Returns a merged SendResult."""
    data = {'event_id': event.id, **(data or {})}
    ios, android = _recipients(event)

    if silent:
        apns_payload = {'aps': {'content-available': 1}, **data}
        r1 = send_apns(ios, apns_payload, push_type='background', priority='5')
    else:
        apns_payload = {'aps': {'alert': alert, 'sound': 'default'}, **data}
        r1 = send_apns(ios, apns_payload)
    r2 = send_fcm(android, data, notification=None if silent else alert)

    _forget(r1.invalid_tokens + r2.invalid_tokens)
    return SendResult(
        delivered=r1.delivered + r2.delivered,
        failed=r1.failed + r2.failed,
        skipped=r1.skipped + r2.skipped,
    )


def send_notification(notification):
    """Send an organizer notification now and mark it sent (idempotent)."""
    with transaction.atomic():
        locked = Notification.objects.select_for_update().get(pk=notification.pk)
        if locked.sent:
            return locked
        result = deliver(
            locked.event,
            alert={'title': locked.title, 'body': locked.body},
            data={'type': 'notification', 'notification_id': locked.id},
        )
        locked.sent = True
        locked.sent_at = timezone.now()
        locked.delivered_count = result.delivered
        locked.failed_count = result.failed
        locked.save()
    logger.info('Notification %s sent: %d delivered, %d failed, %d skipped',
                locked.pk, result.delivered, result.failed, result.skipped)
    return locked


def send_due_notifications(now=None):
    now = now or timezone.now()
    due = Notification.objects.filter(
        sent=False, send_time__lte=now, send_time__gte=now - timedelta(days=2),
    ).exclude(event__status=Event.STATUS_DRAFT)
    count = 0
    for n in due:
        send_notification(n)
        count += 1
    return count


def notify_content_updated(event):
    return deliver(event, data={'type': 'content_updated', 'content_version': event.content_version},
                   silent=True)
