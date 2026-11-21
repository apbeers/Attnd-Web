"""
Push delivery through APNs (iOS) and FCM HTTP v1 (Android).

Configuration (all optional; a platform with no credentials is skipped and logged):

    APNS_AUTH_KEY      contents of the .p8 key (literal "\\n" sequences are accepted)
    APNS_KEY_ID        key ID of that .p8 key
    APNS_TEAM_ID       defaults to APPLE_TEAM_ID
    APNS_TOPIC         app bundle ID, defaults to APPLE_APP_BUNDLE_ID
    APNS_CLIP_TOPIC    the App Clip's bundle ID (API-019), defaults to "<APNS_TOPIC>.clip"
    APNS_USE_SANDBOX   truthy → api.sandbox.push.apple.com (Xcode / TestFlight dev builds)

    FCM_PROJECT_ID              Firebase project ID
    FCM_SERVICE_ACCOUNT_JSON    service account JSON; defaults to GOOGLE_SERVICE_ACCOUNT_JSON
"""

import json
import logging
import threading
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


def send_apns(tokens, payload, push_type='alert', priority='10', collapse_id=None, expiration=None, topic=None):
    """Send one payload to iOS tokens. ``collapse_id`` → ``apns-collapse-id`` (APNs keeps only the latest
    with the same id); ``expiration`` (UNIX seconds) → ``apns-expiration``; ``topic`` overrides the app's
    bundle id (Live Activities use ``<bundle id>.push-type.liveactivity``)."""
    result = SendResult()
    cfg = _apns_config()
    if cfg is None:
        result.skipped = len(tokens)
        if tokens:
            logger.warning('APNs not configured; skipped %d iOS devices', len(tokens))
        return result
    headers = {
        'authorization': f'bearer {_apns_token(cfg)}',
        'apns-topic': topic or cfg['topic'],
        'apns-push-type': push_type,
        'apns-priority': priority,
    }
    if collapse_id:
        headers['apns-collapse-id'] = collapse_id[:64]
    if expiration is not None:
        headers['apns-expiration'] = str(int(expiration))
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


def apns_topic(cfg, app_clip=False, live_activity=False):
    """API-019: the ``apns-topic`` for a token: the app's bundle id, or the App Clip's (``APNS_CLIP_TOPIC``, default
    ``<app topic>.clip``); Live Activity tokens add ``.push-type.liveactivity``. None without an APNs config."""
    if cfg is None:
        return None
    topic = cfg['topic']
    if app_clip:
        topic = getattr(settings, 'APNS_CLIP_TOPIC', '') or f'{topic}.clip'
    return f'{topic}.push-type.liveactivity' if live_activity else topic


def ios_token_groups(devices, field='push_token'):
    """API-019: the iOS devices' tokens (``field``) split by bundle: ``(app tokens, App Clip tokens)``."""
    app, clip = [], []
    for d in devices:
        token = getattr(d, field)
        if d.platform == 'ios' and token:
            (clip if d.app_clip else app).append(token)
    return app, clip


def send_apns_groups(groups, payload, live_activity=False, **kwargs):
    """Send one payload to ``(app tokens, App Clip tokens)``, each on its own topic. The app's group is always
    sent (so an unconfigured APNs still counts it as skipped); the clip's only when it has tokens."""
    cfg = _apns_config()
    app, clip = groups
    result = send_apns(app, payload, topic=apns_topic(cfg, live_activity=live_activity), **kwargs)
    if clip:
        r = send_apns(clip, payload, topic=apns_topic(cfg, app_clip=True, live_activity=live_activity), **kwargs)
        result = SendResult(delivered=result.delivered + r.delivered, failed=result.failed + r.failed,
                            skipped=result.skipped + r.skipped,
                            invalid_tokens=result.invalid_tokens + r.invalid_tokens)
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


def send_fcm(tokens, data, notification=None, priority='high', collapse_key=None, ttl=None):
    """Send one message to Android tokens. ``priority`` is the Android delivery priority ('high' or
    'normal'); ``collapse_key`` makes FCM keep only the latest; ``ttl`` is in seconds."""
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
    android = {'priority': priority}
    if collapse_key:
        android['collapse_key'] = collapse_key
    if ttl is not None:
        android['ttl'] = f'{int(ttl)}s'
    with httpx.Client(timeout=10) as client:
        for token in tokens:
            message = {'token': token, 'data': {k: str(v) for k, v in data.items()},
                       'android': dict(android)}
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

# Silent content pushes are dropped by APNs / FCM after this long: a stale "content changed" is useless,
# the app refreshes on resume anyway.
CONTENT_PUSH_TTL = 3600


def _recipients(event, silent=False, devices=None):
    """Push tokens: ``((iOS app, iOS App Clip), Android)`` (API-019). Visible alerts go only to devices that opted
    in (``subscribed``); silent ``content_updated`` pushes go to every device in the event with a token (API-007).
    `devices` narrows them (a queryset of the event's devices: attendees or preview phones, API-026)."""
    base = devices if devices is not None else Device.objects.real()
    devices = list(base.filter(event=event).exclude(push_token='')
                   .filter(**({} if silent else {'subscribed': True})))
    android = [d.push_token for d in devices if d.platform == 'android']
    return ios_token_groups(devices), android


def _forget(tokens):
    if tokens:
        Device.objects.filter(push_token__in=tokens).update(push_token='')


def deliver(event, alert=None, data=None, silent=False, urgent=False, devices=None):
    """Send one message to the event's devices. Returns a merged SendResult.

    Alerts go to subscribed devices only. Silent pushes go to every device with a token, collapse per
    event (only the latest is delivered) and expire after ``CONTENT_PUSH_TTL``. Urgent alerts (API-011) go to
    every device with a token, ``subscribed`` or not: APNs time-sensitive, FCM high-priority data message.
    """
    data = {'event_id': event.id, **(data or {})}
    ios, android = _recipients(event, silent=silent or urgent, devices=devices)

    if urgent:
        apns_payload = {'aps': {'alert': alert, 'sound': 'default', 'interruption-level': 'time-sensitive',
                                'relevance-score': 1.0}, **data}
        r1 = send_apns_groups(ios, apns_payload, collapse_id=f"notification-{data.get('notification_id', '')}",
                       expiration=time.time() + URGENT_TTL)
        # Data only, so the app picks its urgent channel even in the background. Builds before API-011 show
        # data messages too: they read title/body from the data when there is no notification block.
        r2 = send_fcm(android, {**data, 'title': alert.get('title', ''), 'body': alert.get('body', '')},
                      priority='high', ttl=URGENT_TTL)
    elif silent:
        apns_payload = {'aps': {'content-available': 1}, **data}
        r1 = send_apns_groups(ios, apns_payload, push_type='background', priority='5',
                       collapse_id=f'content-{event.id}', expiration=time.time() + CONTENT_PUSH_TTL)
        r2 = send_fcm(android, data, priority='normal', collapse_key=f'content_{event.id}',
                      ttl=CONTENT_PUSH_TTL)
    else:
        apns_payload = {'aps': {'alert': alert, 'sound': 'default'}, **data}
        r1 = send_apns_groups(ios, apns_payload)
        r2 = send_fcm(android, data, notification=alert)

    _forget(r1.invalid_tokens + r2.invalid_tokens)
    return SendResult(
        delivered=r1.delivered + r2.delivered,
        failed=r1.failed + r2.failed,
        skipped=r1.skipped + r2.skipped,
    )


# Urgent announcements (API-011). They interrupt everyone in the event, so an event may send at most
# URGENT_MAX_PER_HOUR of them (the console refuses more; a scheduled one over the limit goes out as normal).
URGENT_MAX_PER_HOUR = 3
URGENT_TTL = 12 * 3600  # a stale urgent alert is useless; the Live Activity shows it for 12 h at most


def urgent_sends_left(event, now=None):
    """How many more urgent announcements the event may send in the current hour."""
    now = now or timezone.now()
    limit = getattr(settings, 'URGENT_MAX_PER_HOUR', URGENT_MAX_PER_HOUR)
    used = Notification.objects.filter(event=event, sent=True, priority=Notification.PRIORITY_URGENT,
                                       sent_at__gte=now - timedelta(hours=1)).count()
    return max(0, limit - used)


def send_notification(notification):
    """Send an organizer notification now and mark it sent (idempotent).

    An urgent one (API-011) goes to every device in the event with a push token at high priority, and to
    every iOS Live Activity registered for the event."""
    with transaction.atomic():
        locked = Notification.objects.select_for_update().get(pk=notification.pk)
        if locked.sent:
            return locked
        now = timezone.now()
        if locked.is_urgent and urgent_sends_left(locked.event, now) <= 0:
            logger.warning('Notification %s: urgent limit reached for event %s, sending as normal',
                           locked.pk, locked.event_id)
            locked.priority = Notification.PRIORITY_NORMAL
        alert = {'title': locked.title, 'body': locked.body}
        result = deliver(
            locked.event, alert=alert,
            data={'type': 'notification', 'notification_id': locked.id, 'priority': locked.priority},
            urgent=locked.is_urgent,
        )
        if locked.is_urgent:
            la = send_live_activity_urgent(locked.event, locked, now)
            result.delivered += la.delivered
            result.failed += la.failed
        locked.sent = True
        locked.sent_at = now
        locked.delivered_count = result.delivered
        locked.failed_count = result.failed
        locked.save()
    logger.info('Notification %s (%s) sent: %d delivered, %d failed, %d skipped',
                locked.pk, locked.priority, result.delivered, result.failed, result.skipped)
    return locked


def live_activity_payload(notification, now):
    """APNs ``liveactivity`` update that puts an urgent announcement on the event's Live Activity.

    ActivityKit decodes ``content-state`` into the app's ContentState with a default JSONDecoder, so every
    value is a plain JSON string or number (``sent_at`` is an ISO 8601 string, not a Date). The update
    replaces the whole content state: only ``urgent`` is set, the app's own fields decode as absent."""
    sent_at = int(now.timestamp())
    return {'aps': {
        'timestamp': sent_at,
        'event': 'update',
        'content-state': {'urgent': {'id': notification.id, 'title': notification.title, 'body': notification.body,
                                     'sent_at': now.isoformat()}},
        'alert': {'title': notification.title, 'body': notification.body, 'sound': 'default'},
        'stale-date': sent_at + URGENT_TTL,
        'relevance-score': 100,
    }}


def send_live_activity_urgent(event, notification, now=None):
    """Send the urgent announcement to every Live Activity token registered in the event (iOS)."""
    now = now or timezone.now()
    groups = ios_token_groups(Device.objects.real().filter(event=event, platform='ios').exclude(live_activity_token=''),
                              field='live_activity_token')
    if not any(groups):
        return SendResult()
    result = send_apns_groups(groups, live_activity_payload(notification, now), live_activity=True,
                              push_type='liveactivity', priority='10', expiration=now.timestamp() + URGENT_TTL)
    if result.invalid_tokens:
        Device.objects.filter(live_activity_token__in=result.invalid_tokens).update(live_activity_token='')
    return result


def send_due_notifications(now=None):
    now = now or timezone.now()
    due = Notification.objects.filter(
        sent=False, send_time__lte=now, send_time__gte=now - timedelta(days=2),
    ).exclude(event__status=Event.STATUS_DRAFT)
    count = 0
    for n in due:
        send_notification(n)
        count += 1
        try:  # stage I16: tell the organizers it went out (each can turn it off in Account)
            from console import organizer_mail
            n.refresh_from_db()
            organizer_mail.notification_sent(n)
        except Exception:
            logger.exception('Organizer email for notification %s failed', n.pk)
    return count


def jitter_ms(n_devices):
    """API-026: the random wait apps take before refetching, scaled to the event's size (real devices with a push
    token): under 100 phones 0–1 s, under 1,000 0–5 s, else the full 0–10 s. Sent in the push as `jitter_ms`."""
    if n_devices < 100:
        return 1000
    if n_devices < 1000:
        return 5000
    return 10000


def _preview_devices(event):
    return Device.objects.real().filter(event=event).exclude(preview_by__isnull=True, preview_until__isnull=True)


def notify_previews(event):
    """§4: a draft change reaches only the event's preview phones (no random wait)."""
    data = {'type': 'content_updated', 'content_version': event.content_version, 'jitter_ms': 0}
    return deliver(event, data=data, silent=True, devices=_preview_devices(event))


def notify_content_updated(event):
    """The silent `content_updated` push: attendees with `jitter_ms` scaled to the event, preview phones with 0."""
    attendees = Device.objects.attendees()
    n = attendees.filter(event=event).exclude(push_token='').count()
    data = {'type': 'content_updated', 'content_version': event.content_version}
    result = deliver(event, data={**data, 'jitter_ms': jitter_ms(n)}, silent=True, devices=attendees)
    previews = _preview_devices(event)
    if previews.exclude(push_token='').exists():
        more = deliver(event, data={**data, 'jitter_ms': 0}, silent=True, devices=previews)
        result = SendResult(delivered=result.delivered + more.delivered, failed=result.failed + more.failed,
                            skipped=result.skipped + more.skipped)
    return result


# ---------------------------------------------------------------------------
# Live updates: a silent push whenever attendee-visible content changes
# ---------------------------------------------------------------------------
# Push is the primary live-update signal (API-007): it goes to every device in the event with a token,
# and apps refetch only when the pushed content_version is newer. Edits come in bursts (an organizer
# typing, an AI update applying many changes), so pushes are grouped per event (API-026): the first change
# goes out at once (after CONTENT_PUSH_DELAY, default 0), later ones within CONTENT_PUSH_MIN_INTERVAL seconds
# become one follow-up push. The pushes collapse per event, so a device that was offline only gets the latest.

_timers = {}
_last_sent = {}
_timers_lock = threading.Lock()


def content_changed(event_id):
    """Schedule a debounced silent 'content_updated' push for the event (after the transaction commits)."""
    if not getattr(settings, 'CONTENT_PUSH_ENABLED', True):
        return
    transaction.on_commit(lambda: _schedule(event_id))


def _schedule(event_id):
    """API-026: the first change goes out at once; changes while a follow-up waits join it; a change within
    CONTENT_PUSH_MIN_INTERVAL of the last push waits for the rest of it (one grouped follow-up)."""
    delay = getattr(settings, 'CONTENT_PUSH_DELAY', 0)
    min_interval = getattr(settings, 'CONTENT_PUSH_MIN_INTERVAL', 15)
    with _timers_lock:
        if event_id in _timers:
            return  # a follow-up is already waiting; it sends the newest content_version
        since_last = time.monotonic() - _last_sent.get(event_id, -10 ** 9)
        timer = threading.Timer(max(delay, min_interval - since_last), _send_content_push, args=[event_id])
        timer.daemon = True
        _timers[event_id] = timer
        timer.start()


def _send_content_push(event_id):
    from django.db import close_old_connections
    with _timers_lock:
        _timers.pop(event_id, None)
        _last_sent[event_id] = time.monotonic()
    try:
        event = Event.objects.filter(pk=event_id).first()
        if event is None:
            return
        from events.models import PublishedBundle
        published = PublishedBundle.objects.filter(event=event).values_list('version', flat=True).first()
        if event.status == Event.STATUS_DRAFT or (published is not None and published < event.content_version):
            notify_previews(event)  # §4: a draft change: only the preview phones (attendees wait for Publish)
            return
        notify_content_updated(event)
        from events import plan_suggestions
        plan_suggestions.schedule(event_id)  # API-021: rewrite them once the edits settle (background)
    except Exception:
        logger.exception('Content push for event %s failed', event_id)
    finally:
        close_old_connections()
