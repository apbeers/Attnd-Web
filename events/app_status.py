"""GET /api/app-status/: should this app build update? (API_CHANGES.md, API-003)

Deliberately outside every API version and unauthenticated. An app whose API version has been
retired, or whose attestation is failing, can still reach it and be told to update instead of just
breaking. Its response shape is frozen: only add keys, never change or remove them.

Query: platform=ios|android, version=<app version, e.g. 2.0.85>, api=<API version the build speaks>
Answer: {"update": "none" | "available" | "required", "min_version", "latest_version", "update_url", "message"}
"""
import re

from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.http import require_GET


def parse_version(value):
    """'2.0.85' -> (2, 0, 85). Non-numeric parts are ignored; None if there's nothing to compare."""
    parts = re.findall(r'\d+', str(value or '').split('-')[0].split('+')[0])
    return tuple(int(p) for p in parts) or None


def _older(version, than):
    a, b = parse_version(version), parse_version(than)
    if a is None or b is None:
        return False
    width = max(len(a), len(b))
    return a + (0,) * (width - len(a)) < b + (0,) * (width - len(b))


def update_url(platform):
    if platform == 'ios':
        return settings.IOS_APP_STORE_URL or 'https://attnd.app/'
    return settings.ANDROID_PLAY_STORE_URL


def app_status(platform, version, api=None):
    platform = platform if platform in ('ios', 'android') else ''
    min_version = getattr(settings, f'APP_MIN_VERSION_{platform.upper()}', '') if platform else ''
    latest = getattr(settings, f'APP_LATEST_VERSION_{platform.upper()}', '') if platform else ''
    try:
        api_supported = api in (None, '') or int(api) in settings.SUPPORTED_API_VERSIONS
    except (TypeError, ValueError):
        api_supported = True

    if not api_supported or (min_version and _older(version, min_version)):
        update = 'required'
    elif latest and _older(version, latest):
        update = 'available'
    else:
        update = 'none'
    return {
        'update': update,
        'min_version': min_version or None,
        'latest_version': latest or None,
        'update_url': update_url(platform),
        'message': settings.APP_UPDATE_MESSAGE or None,
    }


@require_GET
def app_status_view(request):
    data = app_status(request.GET.get('platform', ''), request.GET.get('version', ''), request.GET.get('api'))
    response = JsonResponse(data)
    response['Cache-Control'] = 'no-cache'
    return response


def retired_api_view(request, version):
    """Catch-all for /api/vN/... paths no route matched.

    A retired API version answers 410 Gone with `"error": "update_required"`, which the apps treat
    like an `update: required` app status. Unknown paths in a live version get a plain JSON 404.
    """
    if int(version) in settings.SUPPORTED_API_VERSIONS:
        return JsonResponse({'error': 'Not found'}, status=404)
    platform = 'ios' if 'darwin' in request.headers.get('User-Agent', '').lower() else 'android'
    return JsonResponse({'error': 'update_required', 'update_url': update_url(platform)}, status=410)
