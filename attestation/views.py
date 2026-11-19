"""
Django views for the attestation app.

URL pattern summary
-------------------
GET  /api/v1/attest/nonce/          → generate a one-time challenge
POST /api/v1/attest/ios/            → verify Apple App Attest attestation
POST /api/v1/attest/android/        → verify Google Play Integrity token
"""

import json
import logging
import secrets
from datetime import timedelta

from django.conf import settings
from django.http import JsonResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from attestation.ios_attest import verify_attestation, AttestationError
from attestation.android_attest import verify_integrity_token, AndroidAttestationError
from attestation.models import AttestationNonce, AttestedDevice
from attestation import tokens
from attnd.ratelimit import RateLimit, client_ip

logger = logging.getLogger(__name__)

# Nonce TTL in seconds — client must attest within this window after fetching nonce
_NONCE_TTL = 300  # 5 minutes
def _issue_nonce() -> str:
    """Create a cryptographically random one-time challenge (base64url, valid as a Play Integrity nonce)."""
    nonce = secrets.token_urlsafe(32)
    AttestationNonce.objects.create(nonce=nonce)
    return nonce


def _consume_nonce(nonce: str) -> bool:
    """True exactly once for a nonce issued within the last _NONCE_TTL seconds (atomic delete)."""
    if not nonce or len(nonce) > 64:
        return False
    fresh_after = timezone.now() - timedelta(seconds=_NONCE_TTL)
    deleted, _ = AttestationNonce.objects.filter(nonce=nonce, created_at__gte=fresh_after).delete()
    return deleted > 0


def purge_expired_nonces() -> int:
    deleted, _ = AttestationNonce.objects.filter(
        created_at__lt=timezone.now() - timedelta(seconds=_NONCE_TTL)).delete()
    return deleted


NONCE_LIMIT = RateLimit('attest_nonce_ip')  # settings.RATE_LIMITS['attest_nonce_ip'], 30 per minute


# ---------------------------------------------------------------------------
# Nonce endpoint
# ---------------------------------------------------------------------------

@method_decorator(csrf_exempt, name='dispatch')
class NonceView(View):
    """GET /api/v1/attest/nonce/ — returns a one-time challenge."""

    def get(self, request):
        ip = client_ip(request)
        if NONCE_LIMIT.exceeded(ip):
            return JsonResponse({'error': 'Too many requests'}, status=429)
        NONCE_LIMIT.hit(ip)
        nonce = _issue_nonce()
        return JsonResponse({'nonce': nonce})


# ---------------------------------------------------------------------------
# iOS attestation
# ---------------------------------------------------------------------------

@method_decorator(csrf_exempt, name='dispatch')
class IOSAttestView(View):
    """
    POST /api/v1/attest/ios/

    Body (JSON):
        key_id      – base64url string from DCAppAttestService.generateKey()
        attestation – base64-encoded CBOR attestation object
        nonce       – the challenge returned by NonceView
    """

    def post(self, request):
        try:
            body = json.loads(request.body)
        except (json.JSONDecodeError, ValueError):
            return JsonResponse({'error': 'Invalid JSON'}, status=400)

        key_id = body.get('key_id', '').strip()
        attestation_b64 = body.get('attestation', '').strip()
        nonce = body.get('nonce', '').strip()

        if not key_id or not attestation_b64 or not nonce:
            return JsonResponse({'error': 'key_id, attestation, and nonce are required'}, status=400)

        if not _consume_nonce(nonce):
            return JsonResponse({'error': 'Invalid or expired nonce'}, status=400)

        bundle_id = getattr(settings, 'APPLE_APP_BUNDLE_ID', '')
        team_id = getattr(settings, 'APPLE_TEAM_ID', '')
        environment = getattr(settings, 'APPLE_ATTEST_ENVIRONMENT', 'production')

        if not bundle_id or not team_id:
            logger.error("APPLE_APP_BUNDLE_ID or APPLE_TEAM_ID not configured")
            return JsonResponse({'error': 'Server misconfiguration'}, status=500)

        try:
            result = verify_attestation(
                key_id=key_id,
                attestation_b64=attestation_b64,
                challenge=nonce,
                bundle_id=bundle_id,
                team_id=team_id,
                environment=environment,
            )
        except AttestationError as exc:
            logger.warning("iOS attestation failed: %s", exc)
            return JsonResponse({'error': str(exc)}, status=400)
        except Exception as exc:
            logger.exception("Unexpected error during iOS attestation")
            return JsonResponse({'error': 'Internal error'}, status=500)

        device, created = AttestedDevice.objects.get_or_create(
            device_key=result.key_id,
            defaults={
                'platform': AttestedDevice.PLATFORM_IOS,
                'public_key_pem': result.public_key_pem,
                'sign_count': 0,
            },
        )
        if not created:
            # Re-attestation — update the stored key
            device.public_key_pem = result.public_key_pem
            device.sign_count = 0
            device.touch()

        token = tokens.issue(device_key=result.key_id, platform='ios')
        return JsonResponse({'token': token})


# ---------------------------------------------------------------------------
# Android attestation
# ---------------------------------------------------------------------------

@method_decorator(csrf_exempt, name='dispatch')
class AndroidAttestView(View):
    """
    POST /api/v1/attest/android/

    Body (JSON):
        device_id – UUID string identifying the device (app-generated)
        token     – Play Integrity token from IntegrityManager
        nonce     – the challenge returned by NonceView
    """

    def post(self, request):
        try:
            body = json.loads(request.body)
        except (json.JSONDecodeError, ValueError):
            return JsonResponse({'error': 'Invalid JSON'}, status=400)

        device_id = body.get('device_id', '').strip()
        integrity_token = body.get('token', '').strip()
        nonce = body.get('nonce', '').strip()

        if not device_id or not integrity_token or not nonce:
            return JsonResponse({'error': 'device_id, token, and nonce are required'}, status=400)

        if not _consume_nonce(nonce):
            return JsonResponse({'error': 'Invalid or expired nonce'}, status=400)

        try:
            result = verify_integrity_token(token=integrity_token, nonce=nonce)
        except AndroidAttestationError as exc:
            logger.warning("Android attestation failed: %s", exc)
            return JsonResponse({'error': str(exc)}, status=400)
        except Exception as exc:
            logger.exception("Unexpected error during Android attestation")
            return JsonResponse({'error': 'Internal error'}, status=500)

        # Store the weakest verdict label for auditing
        verdict_label = result['device_verdict'][0] if result['device_verdict'] else ''

        device, created = AttestedDevice.objects.get_or_create(
            device_key=device_id,
            defaults={
                'platform': AttestedDevice.PLATFORM_ANDROID,
                'android_verdict': verdict_label,
            },
        )
        if not created:
            device.android_verdict = verdict_label
            device.touch()

        token = tokens.issue(device_key=device_id, platform='android')
        return JsonResponse({'token': token})
