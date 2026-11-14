"""Time-based one-time passwords (RFC 6238 TOTP over RFC 4226 HOTP), standard library only.

Compatible with authenticator apps (Google Authenticator, 1Password, Authy, iOS Passwords …):
SHA-1, 6 digits, 30-second steps, base32 secret in an otpauth:// URI.
"""

import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote, urlencode

STEP = 30
DIGITS = 6


def new_secret() -> str:
    """A random 160-bit secret, base32 without padding (what authenticator apps expect)."""
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip('=')


def _key(secret_b32: str) -> bytes:
    cleaned = secret_b32.replace(' ', '').upper()
    return base64.b32decode(cleaned + '=' * (-len(cleaned) % 8))


def hotp(key: bytes, counter: int, digits: int = DIGITS, digest=hashlib.sha1) -> str:
    mac = hmac.new(key, struct.pack('>Q', counter), digest).digest()
    offset = mac[-1] & 0x0F
    code = (struct.unpack('>I', mac[offset:offset + 4])[0] & 0x7FFFFFFF) % (10 ** digits)
    return str(code).zfill(digits)


def time_step(at: float | None = None, step: int = STEP) -> int:
    return int((time.time() if at is None else at) // step)


def totp(key: bytes, at: float | None = None, step: int = STEP, digits: int = DIGITS, digest=hashlib.sha1) -> str:
    return hotp(key, time_step(at, step), digits, digest)


def match_step(secret_b32: str, code: str, after_step: int | None = None, at: float | None = None,
               window: int = 1) -> int | None:
    """The time step `code` belongs to (now ±`window` steps), or None. Steps at or before `after_step`
    (the last one used) never match, so a code can't be used twice."""
    code = ''.join(ch for ch in str(code) if not ch.isspace())
    if len(code) != DIGITS or not code.isdigit():
        return None
    try:
        key = _key(secret_b32)
    except (ValueError, TypeError):
        return None
    now = time_step(at)
    for candidate in range(now - window, now + window + 1):
        if after_step is not None and candidate <= after_step:
            continue
        if hmac.compare_digest(hotp(key, candidate), code):
            return candidate
    return None


def provisioning_uri(secret_b32: str, account: str, issuer: str) -> str:
    label = quote(f'{issuer}:{account}', safe='@:')
    query = urlencode({'secret': secret_b32, 'issuer': issuer, 'algorithm': 'SHA1',
                       'digits': DIGITS, 'period': STEP}, quote_via=quote)
    return f'otpauth://totp/{label}?{query}'
