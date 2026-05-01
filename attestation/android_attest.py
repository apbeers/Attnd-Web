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
appIntegrity.appRecognitionVerdict  PLAY_RECOGNIZED (or UNRECOGNIZED_VERSION in dev)
deviceIntegrity.deviceRecognitionVerdict  contains MEETS_DEVICE_INTEGRITY (minimum)
"""

import base64
import logging

import requests as http_requests
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
        resp = http_requests.post(
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
    except http_requests.HTTPError as exc:
        raise AndroidAttestationError(
            f"Google Play Integrity API request failed: {exc.response.status_code} {exc.response.text}"
        ) from exc
    except Exception as exc:
        raise AndroidAttestationError(f"Play Integrity API error: {exc}") from exc

    payload = data.get('tokenPayloadExternal', {})

    # --- Check nonce ---
    request_details = payload.get('requestDetails', {})
    token_nonce_b64 = request_details.get('nonce', '')
    # The nonce in the token is base64-encoded; our nonce is the raw string.
    # Android SDK base64-encodes the nonce before embedding it.
    expected_nonce_b64 = base64.b64encode(nonce.encode()).decode()
    if token_nonce_b64 != expected_nonce_b64:
        raise AndroidAttestationError(
            f"Nonce mismatch: token has {token_nonce_b64!r}, expected {expected_nonce_b64!r}"
        )

    # --- Check package name ---
    token_pkg = request_details.get('requestPackageName', '')
    if token_pkg != pkg:
        raise AndroidAttestationError(
            f"Package name mismatch: token has {token_pkg!r}, expected {pkg!r}"
        )

    # --- Check app integrity ---
    app_integrity = payload.get('appIntegrity', {})
    app_verdict = app_integrity.get('appRecognitionVerdict', '')
    if app_verdict not in ('PLAY_RECOGNIZED', 'UNRECOGNIZED_VERSION'):
        raise AndroidAttestationError(
            f"App not recognized by Play: appRecognitionVerdict={app_verdict!r}"
        )

    # --- Check device integrity ---
    device_integrity = payload.get('deviceIntegrity', {})
    device_verdicts: list[str] = device_integrity.get('deviceRecognitionVerdict', [])
    required = _STRONG_VERDICT if require_strong_integrity else _MIN_VERDICT
    if required not in device_verdicts:
        raise AndroidAttestationError(
            f"Device integrity insufficient: {device_verdicts!r} (required {required!r})"
        )

    logger.info(
        "Android Play Integrity verified: pkg=%s app=%s device=%s",
        pkg,
        app_verdict,
        device_verdicts,
    )

    return {
        'verdict': payload,
        'device_verdict': device_verdicts,
        'app_verdict': app_verdict,
    }


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
