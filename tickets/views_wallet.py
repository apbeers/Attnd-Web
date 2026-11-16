"""Apple's PassKit web service (https://developer.apple.com/documentation/walletpasses/adding-a-web-service-to-update-passes).

webServiceURL = SITE_URL/wallet/, so the device calls:
    POST/DELETE /wallet/v1/devices/<device>/registrations/<passTypeId>/<serial>   (Authorization: ApplePass <token>)
    GET         /wallet/v1/devices/<device>/registrations/<passTypeId>?passesUpdatedSince=<tag>
    GET         /wallet/v1/passes/<passTypeId>/<serial>                             (Authorization: ApplePass <token>)
    POST        /wallet/v1/log
"""

import hmac
import json
import logging
from datetime import datetime, timezone as dt_timezone

from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.utils.http import http_date, parse_http_date_safe
from django.views.decorators.csrf import csrf_exempt

from tickets import wallet
from tickets.models import WalletPass, WalletRegistration

logger = logging.getLogger(__name__)


def _authorized(request, pass_type, serial):
    if pass_type != settings.APPLE_WALLET_PASS_TYPE_ID:
        return None
    wallet_pass = WalletPass.objects.filter(serial=serial, platform=WalletPass.APPLE).select_related(
        'ticket', 'ticket__event', 'ticket__ticket_type', 'ticket__order').first()
    header = request.headers.get('Authorization', '')
    if wallet_pass is None or not header.startswith('ApplePass ') or not hmac.compare_digest(
            header[len('ApplePass '):].strip(), wallet_pass.auth_token):
        return None
    return wallet_pass


@csrf_exempt
def registration(request, device, pass_type, serial):
    wallet_pass = _authorized(request, pass_type, serial)
    if wallet_pass is None:
        return HttpResponse(status=401)
    if request.method == 'DELETE':
        WalletRegistration.objects.filter(wallet_pass=wallet_pass, device_library_id=device[:128]).delete()
        return HttpResponse(status=200)
    if request.method != 'POST':
        return HttpResponse(status=405)
    try:
        push_token = str(json.loads(request.body or b'{}').get('pushToken', ''))[:256]
    except (ValueError, AttributeError):
        return HttpResponse(status=400)
    if not push_token:
        return HttpResponse(status=400)
    _, created = WalletRegistration.objects.update_or_create(
        wallet_pass=wallet_pass, device_library_id=device[:128], defaults={'push_token': push_token})
    return HttpResponse(status=201 if created else 200)


def _tag(dt):
    return str(int(dt.timestamp() * 1_000_000))  # opaque to Apple; microseconds so nothing is missed


def serials(request, device, pass_type):
    if pass_type != settings.APPLE_WALLET_PASS_TYPE_ID:
        return HttpResponse(status=404)
    qs = WalletPass.objects.filter(registrations__device_library_id=device[:128], platform=WalletPass.APPLE)
    since = request.GET.get('passesUpdatedSince', '')
    if since.isdigit():
        qs = qs.filter(updated_at__gt=datetime.fromtimestamp(int(since) / 1_000_000, tz=dt_timezone.utc))
    passes = list(qs.distinct())
    if not passes:
        return HttpResponse(status=204)
    return JsonResponse({'serialNumbers': [p.serial for p in passes],
                         'lastUpdated': _tag(max(p.updated_at for p in passes))})


def latest_pass(request, pass_type, serial):
    wallet_pass = _authorized(request, pass_type, serial)
    if wallet_pass is None:
        return HttpResponse(status=401)
    if not wallet.apple_enabled():
        return HttpResponse(status=404)
    since = parse_http_date_safe(request.headers.get('If-Modified-Since', ''))
    if since is not None and int(wallet_pass.updated_at.timestamp()) <= since:
        return HttpResponse(status=304)
    response = HttpResponse(wallet.apple_pkpass_for(wallet_pass), content_type='application/vnd.apple.pkpass')
    response['Last-Modified'] = http_date(wallet_pass.updated_at.timestamp())
    return response


@csrf_exempt
def log(request):
    try:
        for line in (json.loads(request.body or b'{}').get('logs') or [])[:20]:
            logger.info('Apple Wallet: %s', str(line)[:500])
    except (ValueError, AttributeError):
        pass
    return HttpResponse(status=200)
