import os
from urllib.parse import quote

from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View

from events.models import Event

IOS_APP_STORE_URL = os.getenv('IOS_APP_STORE_URL', 'https://apps.apple.com/app/id<APP_ID>')
ANDROID_PACKAGE_NAME = os.getenv('ANDROID_PACKAGE_NAME', '<PACKAGE_NAME>')


class JoinEventRedirect(View):

    def get(self, request, event_code):
        event = get_object_or_404(Event, event_code=event_code)
        ua = request.headers.get('User-Agent', '').lower()

        android_url = (
            f'https://play.google.com/store/apps/details'
            f'?id={ANDROID_PACKAGE_NAME}'
            f'&referrer={quote(f"event_code={event_code}")}'
        )

        if 'iphone' in ua or 'ipad' in ua or 'ipod' in ua:
            response = redirect(IOS_APP_STORE_URL)
        elif 'android' in ua:
            response = redirect(android_url)
        else:
            return render(request, 'join/landing.html', {
                'event': event,
                'ios_url': IOS_APP_STORE_URL,
                'android_url': android_url,
            })

        response['Cache-Control'] = 'no-cache'
        return response


class AppleAppSiteAssociation(View):

    def get(self, request):
        team_id = os.getenv('APPLE_TEAM_ID', '<TEAM_ID>')
        bundle_id = os.getenv('APPLE_APP_BUNDLE_ID', '<BUNDLE_ID>')
        app_id = f'{team_id}.{bundle_id}'
        clip_id = f'{app_id}.Clip'
        data = {
            'appclips': {
                'apps': [clip_id],
            },
            'applinks': {
                'details': [{
                    'appIDs': [app_id, clip_id],
                    'components': [
                        {'/' : '/join/*', 'comment': 'Join event links'},
                        {'/' : '/*'},
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
        package_name = os.getenv('ANDROID_PACKAGE_NAME', '<PACKAGE_NAME>')
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
