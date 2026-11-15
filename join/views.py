import base64
import binascii
import json
import os
import re
from datetime import date
from urllib.parse import quote

from django.conf import settings
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views import View

from events.models import Event



_BOTS = ('bot', 'crawler', 'spider', 'preview', 'facebookexternalhit', 'slackbot', 'whatsapp', 'telegram',
         'discord', 'embedly', 'skypeuripreview', 'headless')


def client_kind(user_agent):
    """'ios', 'android', 'desktop' or 'bot' from a User-Agent (good enough for routing and counting)."""
    ua = (user_agent or '').lower()
    if any(b in ua for b in _BOTS):
        return 'bot'
    if 'iphone' in ua or 'ipad' in ua or 'ipod' in ua:
        return 'ios'
    if 'android' in ua:
        return 'android'
    return 'desktop'


def _app_store_id():
    m = re.search(r'id(\d+)', settings.IOS_APP_STORE_URL or '')
    return m.group(1) if m else ''


def play_url(event_code):
    # The install referrer carries the code, so the app joins the event on first launch.
    return (f'{settings.ANDROID_PLAY_STORE_URL}'
            f'&referrer={quote(f"event_code={event_code}")}')


def _visible_event(code):
    return Event.objects.filter(event_code__iexact=(code or '').strip()).exclude(status=Event.STATUS_DRAFT).first()


def _not_found(request, code):
    response = render(request, 'join/not_found.html', {'code': code}, status=404)
    response['Cache-Control'] = 'no-cache'
    return response


def _count_open(event, kind, user_agent):
    # 'attnd-ci' marks the live integration tests (live_tests/), which must not inflate the DEMO stats.
    if kind != 'bot' and 'attnd-ci' not in user_agent.lower():
        from events import usage
        usage.record(event, 'link_open', detail=kind)


def _landing(request, event, kind, extra=None):
    """The join landing page (or the Play redirect on Android); `extra` adds a shared plan (API-015)."""
    if kind == 'android':
        response = redirect(play_url(event.event_code))
    else:
        context = {
            'event': event, 'kind': kind,
            'join_url': f'https://attnd.app/join/{event.event_code}/',  # exactly what the QR code encodes
            'qr_url': reverse('join:join_qr', args=[event.event_code]),
            'ios_url': settings.IOS_APP_STORE_URL, 'android_url': play_url(event.event_code),
            'app_store_id': _app_store_id(),
            'clip_bundle_id': f"{os.getenv('APPLE_APP_BUNDLE_ID', 'com.arcu.Attnd')}.clip",
        }
        context.update(extra or {})
        response = render(request, 'join/landing.html', context)
    response['Cache-Control'] = 'no-cache'
    return response


class JoinEventRedirect(View):
    """/join/<code>/: the one link (and QR code) for every device.

    - iPhone with the app: iOS opens the app directly (Universal Links); this page isn't loaded.
    - iPhone without it: scanning with the Camera shows the App Clip card. If the page does load (e.g. in
      Safari), it shows the App Clip banner, an Open button and the App Store link. It doesn't auto-redirect
      to the App Store, which would dismiss the App Clip card.
    - Android with the app: Android opens it directly (App Links); without: redirect to Play with the
      code as install referrer, so the app joins the event on first launch.
    - Desktop: an event page with a QR code to scan with a phone.
    Every open is counted anonymously (UsageCount link_open, detail ios/android/desktop).
    """

    def get(self, request, event_code):
        code = (event_code or '').strip().upper()
        event = _visible_event(code)
        if event is None:
            return _not_found(request, code)
        user_agent = request.headers.get('User-Agent') or ''
        kind = client_kind(user_agent)
        _count_open(event, kind, user_agent)
        return _landing(request, event, kind)


# ---------------------------------------------------------------------------
# Shared plan links (API-015): /p/<EVENT_CODE>/<payload>
# ---------------------------------------------------------------------------

SHARE_PAYLOAD_MAX = 1500
_B64URL = re.compile(r'^[A-Za-z0-9_-]+={0,2}$')


def encode_share_payload(ids, day=None):
    """base64url (no padding) of {"v":1,"ids":[…],"d":"YYYY-MM-DD"?}, compact JSON, as the apps write it."""
    data = {'v': 1, 'ids': list(ids)}
    if day:
        data['d'] = day
    raw = json.dumps(data, separators=(',', ':')).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip('=')


def decode_share_payload(payload):
    """(ids, day) from a share payload, or None when it isn't a valid v1 payload.

    Lenient where it costs nothing: padding is optional, ids may be numeric strings ("12" or "s12"), duplicates and
    non-ids are skipped, and a malformed `d` is ignored. Not a v1 object (or > 1,500 characters) → None.
    """
    payload = (payload or '').strip()
    if not payload or len(payload) > SHARE_PAYLOAD_MAX or not _B64URL.match(payload):
        return None
    try:
        raw = base64.urlsafe_b64decode(payload.rstrip('=') + '=' * (-len(payload.rstrip('=')) % 4))
        data = json.loads(raw.decode('utf-8'))
    except (binascii.Error, ValueError, UnicodeDecodeError):
        return None
    if not isinstance(data, dict) or data.get('v') != 1 or not isinstance(data.get('ids'), list):
        return None
    ids = []
    for value in data['ids']:
        if isinstance(value, str) and re.fullmatch(r'[sS]?\d{1,12}', value.strip()):
            value = int(value.strip().lstrip('sS'))
        if isinstance(value, int) and not isinstance(value, bool) and value > 0 and value not in ids:
            ids.append(value)
    day = None
    if isinstance(data.get('d'), str):
        try:
            day = date.fromisoformat(data['d'])
        except ValueError:
            day = None
    return ids, day


def shared_plan_items(event, ids):
    """The shared ids that are visible schedule items of this event (as in the bundle), in time order."""
    from events.content import models_q_visible_location
    return list(
        event.scheduleditem_set.filter(pk__in=ids, hidden=False).filter(models_q_visible_location())
        .select_related('location').order_by('start_time', 'name', 'pk')
    )


class SharedPlanRedirect(View):
    """/p/<code>/<payload>/: a plan shared from the app (API-015). Same routing as /join/<code>/.

    The apps claim /p/* (Universal Links incl. the App Clip, App Links), so with the app installed this page isn't
    loaded and the app shows the shared plan. Otherwise: Android → Play with the event code as install referrer;
    iPhone → the App Clip banner + App Store link; desktop → the join landing page listing the plan's items (name,
    time in the event's time zone, place) with a QR code of the share link. An invalid payload falls back to the
    plain join page; an unknown event is the join 404. Opens count as `link_open` like join links.
    """

    def get(self, request, event_code, payload):
        code = (event_code or '').strip().upper()
        event = _visible_event(code)
        if event is None:
            return _not_found(request, code)
        user_agent = request.headers.get('User-Agent') or ''
        kind = client_kind(user_agent)
        _count_open(event, kind, user_agent)
        decoded = decode_share_payload(payload)
        if decoded is None:
            return _landing(request, event, kind)
        ids, day = decoded
        payload = payload.strip().rstrip('=')
        share_url = f'https://attnd.app/p/{event.event_code}/{payload}'
        return _landing(request, event, kind, {
            'shared_plan': True,
            'plan_items': shared_plan_items(event, ids),
            'plan_day': day,
            'share_url': share_url,
            'qr_url': reverse('shared_plan_qr', args=[event.event_code, payload]),
        })


def join_qr(request, event_code):
    """SVG QR code for the join link (the desktop page shows it)."""
    import qrcode
    import qrcode.image.svg
    event = Event.objects.filter(event_code__iexact=event_code).exclude(status=Event.STATUS_DRAFT).first()
    if event is None:
        raise Http404
    img = qrcode.make(f'https://attnd.app/join/{event.event_code}/', image_factory=qrcode.image.svg.SvgPathImage,
                      box_size=12, border=2)
    response = HttpResponse(img.to_string(), content_type='image/svg+xml')
    response['Cache-Control'] = 'public, max-age=3600'
    return response


def shared_plan_qr(request, event_code, payload):
    """SVG QR code for a shared plan link (the desktop page shows it)."""
    import qrcode
    import qrcode.image.svg
    event = _visible_event(event_code)
    if event is None or decode_share_payload(payload) is None:
        raise Http404
    url = f'https://attnd.app/p/{event.event_code}/{payload.strip().rstrip("=")}'
    img = qrcode.make(url, image_factory=qrcode.image.svg.SvgPathImage, box_size=12, border=2,
                      error_correction=qrcode.constants.ERROR_CORRECT_L)
    response = HttpResponse(img.to_string(), content_type='image/svg+xml')
    response['Cache-Control'] = 'public, max-age=3600'
    return response


class AppleAppSiteAssociation(View):

    def get(self, request):
        team_id = os.getenv('APPLE_TEAM_ID', '<TEAM_ID>')
        bundle_id = os.getenv('APPLE_APP_BUNDLE_ID', '<BUNDLE_ID>')
        app_id = f'{team_id}.{bundle_id}'
        clip_id = f'{app_id}.clip'
        data = {
            'appclips': {
                'apps': [clip_id],
            },
            'applinks': {
                'details': [{
                    'appIDs': [app_id, clip_id],
                    'components': [
                        {'/': '/join/*/qr.svg', 'exclude': True, 'comment': 'QR images stay on the web'},
                        {'/': '/join/*', 'comment': 'Join event links open the app (or the App Clip)'},
                        {'/': '/p/*/*/qr.svg', 'exclude': True, 'comment': 'QR images stay on the web'},
                        {'/': '/p/*', 'comment': 'Shared plan links open the app (or the App Clip, API-015)'},
                    ],
                }],
            },
        }
        response = JsonResponse(data)
        response['Content-Type'] = 'application/json'
        response['Cache-Control'] = 'no-cache'
        return response


class AndroidAssetLinks(View):
    """Digital Asset Links: domain-wide (handle_all_urls), so it covers /join/* and /p/* (API-015); which paths open the
    app is decided by the app's verified intent filter (android:autoVerify) in its manifest."""

    def get(self, request):
        package_name = settings.ANDROID_PACKAGE_NAME
        fingerprint = os.getenv('ANDROID_SHA256_FINGERPRINT', '<SHA256_FINGERPRINT>')
        data = [{
            'relation': ['delegate_permission/common.handle_all_urls'],
            'target': {
                'namespace': 'android_app',
                'package_name': package_name,
                'sha256_cert_fingerprints': [fingerprint],
            },
        }]
        return JsonResponse(data, safe=False)
