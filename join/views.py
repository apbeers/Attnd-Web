import os
import re
from urllib.parse import quote

from django.conf import settings
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
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
    return (f'https://play.google.com/store/apps/details?id={settings.ANDROID_PACKAGE_NAME}'
            f'&referrer={quote(f"event_code={event_code}")}')


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
        event = Event.objects.filter(event_code__iexact=code).exclude(status=Event.STATUS_DRAFT).first()
        kind = client_kind(request.headers.get('User-Agent'))
        if event is None:
            response = render(request, 'join/not_found.html', {'code': code}, status=404)
            response['Cache-Control'] = 'no-cache'
            return response
        if kind != 'bot':
            from events import usage
            usage.record(event, 'link_open', detail=kind)

        join_url = f'https://attnd.app/join/{event.event_code}/'  # exactly what the QR code encodes
        if kind == 'android':
            response = redirect(play_url(event.event_code))
        else:
            response = render(request, 'join/landing.html', {
                'event': event, 'kind': kind, 'join_url': join_url,
                'ios_url': settings.IOS_APP_STORE_URL, 'android_url': play_url(event.event_code),
                'app_store_id': _app_store_id(),
                'clip_bundle_id': f"{os.getenv('APPLE_APP_BUNDLE_ID', 'com.arcu.Attnd')}.clip",
            })
        response['Cache-Control'] = 'no-cache'
        return response


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
                    ],
                }],
            },
        }
        response = JsonResponse(data)
        response['Content-Type'] = 'application/json'
        response['Cache-Control'] = 'no-cache'
        return response


class AndroidAssetLinks(View):

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
