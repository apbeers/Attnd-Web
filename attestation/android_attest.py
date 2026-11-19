"""
Google Play Integrity API verification.

References:
  https://developer.android.com/google/play/integrity/overview
  https://developer.android.com/google/play/integrity/verdict

Flow
----
1. Client calls GET /api/v1/attest/nonce/ to get a server-generated challenge.
2. Client calls IntegrityManager.requestIntegrityToken(nonce) → integrity token.
3. Client POSTs {device_id, token, nonce} to /api/v1/attest/android/.
4. This module decodes the token using Google's API and checks the verdict.

Token structure (after Google decryption)
------------------------------------------
The integrity token is a JWS (JSON Web Signature) object. We ask Google to
decrypt/verify it server-side via the Play Integrity API rather than doing it
ourselves, because the encryption keys belong to Google.

Verdict fields we check
------------------------
requestDetails.requestPackageName   must match ANDROID_PACKAGE_NAME
requestDetails.nonce                must match the challenge we issued (base64)
requestDetails.timestampMillis      within 10 minutes
appIntegrity.appRecognitionVerdict  PLAY_RECOGNIZED (the unmodified build Play distributed)
appIntegrity.certificateSha256Digest  contains ANDROID_SIGNING_CERT_SHA256 when configured
accountDetails.appLicensingVerdict  LICENSED (installed from Google Play)
deviceIntegrity.deviceRecognitionVerdict  contains MEETS_DEVICE_INTEGRITY (not rooted / not an emulator)
"""

import base64
import logging

import httpx
from django.conf import settings

logger = logging.getLogger(__name__)

# Minimum acceptable device integrity label
_MIN_VERDICT = 'MEETS_DEVICE_INTEGRITY'
# Strongest verdict label (production recommendation)
_STRONG_VERDICT = 'MEETS_STRONG_INTEGRITY'

# Google Play Integrity API endpoint
_DECODE_URL = (
    'https://playintegrity.googleapis.com/v1/{package_name}:decodeIntegrityToken'
)


class AndroidAttestationError(Exception):
    """Raised when Play Integrity verification fails."""


def verify_integrity_token(
    *,
    token: str,
    nonce: str,
    package_name: str | None = None,
    require_strong_integrity: bool = False,
) -> dict:
    """
    Verify a Google Play Integrity token via the server-side API.

    Parameters
    ----------
    token:
        The integrity token string from the Android client.
    nonce:
        The server-generated challenge that was passed to the client.
    package_name:
        Override the package name; defaults to settings.ANDROID_PACKAGE_NAME.
    require_strong_integrity:
        If True, require MEETS_STRONG_INTEGRITY (not just MEETS_DEVICE_INTEGRITY).

    Returns
    -------
    dict with keys:
        verdict          – the raw tokenPayloadExternal dict from Google
        device_verdict   – list of device recognition labels
        app_verdict      – app recognition verdict string

    Raises AndroidAttestationError on any failure.
    """
    pkg = package_name or getattr(settings, 'ANDROID_PACKAGE_NAME', '')
    if not pkg:
        raise AndroidAttestationError("ANDROID_PACKAGE_NAME not configured")

    google_credentials = _get_google_credentials()
    url = _DECODE_URL.format(package_name=pkg)

    try:
        resp = httpx.post(
            url,
            json={'integrity_token': token},
            headers={
                'Authorization': f'Bearer {google_credentials}',
                'Content-Type': 'application/json',
            },
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
    except httpx.HTTPStatusError as exc:
        raise AndroidAttestationError(
            f"Google Play Integrity API request failed: {exc.response.status_code} {exc.response.text}"
        ) from exc
    except Exception as exc:
        raise AndroidAttestationError(f"Play Integrity API error: {exc}") from exc

    payload = data.get('tokenPayloadExternal', {})
    return check_verdict(payload, nonce=nonce, package_name=pkg,
                         require_strong_integrity=require_strong_integrity)


def _cert_digest_b64url(value: str) -> str:
    """Accept a signing-cert SHA-256 as Play Console shows it (AA:BB:…) or as base64url."""
    value = value.strip()
    if ':' in value or (len(value) == 64 and all(c in '0123456789abcdefABCDEF' for c in value)):
        raw = bytes.fromhex(value.replace(':', ''))
        return base64.urlsafe_b64encode(raw).decode().rstrip('=')
    return value.rstrip('=')


def check_verdict(payload: dict, *, nonce: str, package_name: str,
                  require_strong_integrity: bool = False, now_ms: int | None = None) -> dict:
    """
    Enforce: the request is ours and fresh, the app is the unmodified build Google Play knows
    (signed with our key, installed from Play), and the device passes Android integrity checks.

    Settings (all optional):
        ANDROID_ALLOW_UNRECOGNIZED_APP  True only for testing side-loaded / locally built release apps
        ANDROID_REQUIRE_PLAY_LICENSE    default True — app must be installed/updated through Google Play
        ANDROID_SIGNING_CERT_SHA256     Play "App signing key" SHA-256; pins the signing certificate
        ANDROID_REQUIRE_STRONG_INTEGRITY  require MEETS_STRONG_INTEGRITY (hardware-backed)
    """
    import time

    request_details = payload.get('requestDetails', {})

    # --- Our nonce, our package, and recent ---
    token_nonce = (request_details.get('nonce') or '').rstrip('=')
    if not nonce or token_nonce != nonce.rstrip('='):
        raise AndroidAttestationError('Nonce mismatch')
    if request_details.get('requestPackageName') != package_name:
        raise AndroidAttestationError(
            f"Package name mismatch: {request_details.get('requestPackageName')!r}")
    ts = int(request_details.get('timestampMillis') or 0)
    now_ms = now_ms or int(time.time() * 1000)
    if not ts or abs(now_ms - ts) > 10 * 60 * 1000:
        raise AndroidAttestationError('Integrity token is not fresh')

    # --- Unmodified app, signed with our key ---
    app_integrity = payload.get('appIntegrity', {})
    app_verdict = app_integrity.get('appRecognitionVerdict', '')
    allowed = {'PLAY_RECOGNIZED'}
    if getattr(settings, 'ANDROID_ALLOW_UNRECOGNIZED_APP', False):
        allowed.add('UNRECOGNIZED_VERSION')
    if app_verdict not in allowed:
        raise AndroidAttestationError(f'App is not the official Play build: {app_verdict!r}')
    if app_integrity.get('packageName') and app_integrity['packageName'] != package_name:
        raise AndroidAttestationError('App package mismatch')
    pinned = getattr(settings, 'ANDROID_SIGNING_CERT_SHA256', '')
    if pinned:
        digests = [d.rstrip('=') for d in app_integrity.get('certificateSha256Digest', [])]
        if _cert_digest_b64url(pinned) not in digests:
            raise AndroidAttestationError('App is not signed with the Attnd signing key')

    # --- Installed through Google Play ---
    if getattr(settings, 'ANDROID_REQUIRE_PLAY_LICENSE', True):
        licensing = payload.get('accountDetails', {}).get('appLicensingVerdict', '')
        if licensing != 'LICENSED':
            raise AndroidAttestationError(f'App was not installed from Google Play: {licensing!r}')

    # --- Untampered device ---
    device_verdicts: list[str] = payload.get('deviceIntegrity', {}).get('deviceRecognitionVerdict', [])
    strong = require_strong_integrity or getattr(settings, 'ANDROID_REQUIRE_STRONG_INTEGRITY', False)
    required = _STRONG_VERDICT if strong else _MIN_VERDICT
    if required not in device_verdicts:
        raise AndroidAttestationError(
            f'Device integrity insufficient: {device_verdicts!r} (required {required!r})')

    logger.info('Android Play Integrity verified: pkg=%s app=%s device=%s',
                package_name, app_verdict, device_verdicts)
    return {'verdict': payload, 'device_verdict': device_verdicts, 'app_verdict': app_verdict}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_google_credentials() -> str:
    """
    Get a short-lived OAuth2 access token for the Play Integrity API.

    Requires GOOGLE_SERVICE_ACCOUNT_JSON to be set in settings (the full JSON
    key file content as a string), or falls back to application default
    credentials (useful when deployed on GCP/Cloud Run).
    """
    service_account_json = getattr(settings, 'GOOGLE_SERVICE_ACCOUNT_JSON', None)

    try:
        from google.oauth2 import service_account
        from google.auth.transport.requests import Request as GoogleRequest

        if service_account_json:
            import json as _json
            info = _json.loads(service_account_json)
            creds = service_account.Credentials.from_service_account_info(
                info,
                scopes=['https://www.googleapis.com/auth/playintegrity'],
            )
        else:
            import google.auth
            creds, _ = google.auth.default(
                scopes=['https://www.googleapis.com/auth/playintegrity']
            )

        creds.refresh(GoogleRequest())
        return creds.token
    except Exception as exc:
        raise AndroidAttestationError(
            f"Cannot obtain Google credentials for Play Integrity API: {exc}"
        ) from exc
