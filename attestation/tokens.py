"""
Short-lived JWT tokens issued after successful attestation.

Clients include these in the Authorization header:
    Authorization: Bearer <token>

The existing static API_KEY continues to work during the rollout period
so CI tests and development environments don't break.
"""

import logging
from datetime import timedelta

import jwt
from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)

# How long an attestation token is valid before the app must re-attest.
_TTL = timedelta(hours=getattr(settings, 'ATTESTATION_JWT_TTL_HOURS', 24))


def _secret() -> str:
    """Use a dedicated secret if configured, otherwise fall back to SECRET_KEY."""
    return getattr(settings, 'ATTESTATION_JWT_SECRET', None) or settings.SECRET_KEY


def issue(device_key: str, platform: str) -> str:
    """
    Create and sign a JWT for a successfully attested device.
    Returns the compact token string.
    """
    now = timezone.now()
    payload = {
        'sub': device_key,
        'platform': platform,
        'iat': int(now.timestamp()),
        'exp': int((now + _TTL).timestamp()),
        'iss': 'attnd',
    }
    return jwt.encode(payload, _secret(), algorithm='HS256')


def verify(token: str) -> dict | None:
    """
    Verify and decode an attestation JWT.
    Returns the payload dict on success, None on any failure.
    """
    try:
        payload = jwt.decode(
            token,
            _secret(),
            algorithms=['HS256'],
            options={'require': ['sub', 'exp', 'iat', 'iss']},
        )
        if payload.get('iss') != 'attnd':
            return None
        return payload
    except jwt.ExpiredSignatureError:
        logger.debug("Attestation JWT expired")
        return None
    except jwt.InvalidTokenError as exc:
        logger.debug("Invalid attestation JWT: %s", exc)
        return None
