"""
Django views for the attestation app.

URL pattern summary
-------------------
GET  /api/v1/attest/nonce/          → generate a one-time challenge
POST /api/v1/attest/ios/            → verify Apple App Attest attestation
POST /api/v1/attest/android/        → verify Google Play Integrity token
POST /api/v1/attest/ios/assert/     → verify Apple App Attest assertion (per-request auth)
"""

import json
import logging
import secrets

from django.conf import settings
from django.core.cache import cache
from django.http import JsonResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from attestation.ios_attest import verify_attestation, verify_assertion, AttestationError
from attestation.android_attest import verify_integrity_token, AndroidAttestationError
from attestation.models import AttestedDevice
from attestation import tokens

logger = logging.getLogger(__name__)

# Nonce TTL in seconds — client must attest within this window after fetching nonce
_NONCE_TTL = 300  # 5 minutes
_NONCE_CACHE_PREFIX = 'attest:nonce:'


def _nonce_cache_key(nonce: str) -> str:
    return f"{_NONCE_CACHE_PREFIX}{nonce}"


def _issue_nonce() -> str:
    """Create a cryptographically random nonce and store it in cache."""
    nonce = secrets.token_urlsafe(32)
    cache.set(_nonce_cache_key(nonce), True, timeout=_NONCE_TTL)
    return nonce


def _consume_nonce(nonce: str) -> bool:
    """
    Returns True and deletes the nonce if it exists; False if unknown or expired.
    This is atomic enough for our purposes — cache.delete returns whether
    the key was present.
    """
    key = _nonce_cache_key(nonce)
    if cache.get(key):
        cache.delete(key)
        return True
    return False


# ---------------------------------------------------------------------------
# Nonce endpoint
# ---------------------------------------------------------------------------

@method_decorator(csrf_exempt, name='dispatch')
class NonceView(View):
    """GET /api/v1/attest/nonce/ — returns a one-time challenge."""

    def get(self, request):
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


# ---------------------------------------------------------------------------
# iOS assertion (per-request verification)
# ---------------------------------------------------------------------------

@method_decorator(csrf_exempt, name='dispatch')
class IOSAssertView(View):
    """
    POST /api/v1/attest/ios/assert/

    Verifies a DCAppAttestService assertion for a specific API request.
    The client sends this alongside the actual API request body.

    Body (JSON):
        key_id      – base64url key identifier (same as used during attestation)
        assertion   – base64-encoded CBOR assertion from DCAppAttestService
        client_data – the raw JSON bytes that were signed (as a UTF-8 string)

    Returns a new short-lived JWT on success.
    """

    def post(self, request):
        try:
            body = json.loads(request.body)
        except (json.JSONDecodeError, ValueError):
            return JsonResponse({'error': 'Invalid JSON'}, status=400)

        key_id = body.get('key_id', '').strip()
        assertion_b64 = body.get('assertion', '').strip()
        client_data_str = body.get('client_data', '')

        if not key_id or not assertion_b64 or not client_data_str:
            return JsonResponse({'error': 'key_id, assertion, and client_data are required'}, status=400)

        device = AttestedDevice.objects.filter(
            device_key=key_id,
            platform=AttestedDevice.PLATFORM_IOS,
        ).first()
        if device is None:
            return JsonResponse({'error': 'Device not attested'}, status=404)

        bundle_id = getattr(settings, 'APPLE_APP_BUNDLE_ID', '')
        team_id = getattr(settings, 'APPLE_TEAM_ID', '')

        try:
            new_sign_count = verify_assertion(
                assertion_b64=assertion_b64,
                client_data=client_data_str.encode('utf-8'),
                stored_public_key_pem=device.public_key_pem,
                stored_sign_count=device.sign_count,
                bundle_id=bundle_id,
                team_id=team_id,
            )
        except AttestationError as exc:
            logger.warning("iOS assertion failed for key_id=%s: %s", key_id[:16], exc)
            return JsonResponse({'error': str(exc)}, status=400)
        except Exception as exc:
            logger.exception("Unexpected error during iOS assertion")
            return JsonResponse({'error': 'Internal error'}, status=500)

        device.sign_count = new_sign_count
        device.save(update_fields=['sign_count', 'last_seen'])

        token = tokens.issue(device_key=key_id, platform='ios')
        return JsonResponse({'token': token})
