"""Passkeys: a WebAuthn Level 3 relying party, standard library + cbor2 + cryptography only.

What is checked (W3C WebAuthn §7.1 registration, §7.2 authentication):
- clientDataJSON: `type` (webauthn.create / webauthn.get), `challenge` (the one we stored in the session,
  single use, 5 minutes), `origin` (settings.WEBAUTHN_ORIGINS), not `crossOrigin`.
- authenticatorData: rpIdHash == SHA-256(RP ID), User Present and User Verified flags set (UV = the
  device checked a biometric or PIN, which is what makes a passkey count as both factors), BS only with BE.
- Registration: the attested credential data (credential id + COSE public key: EC2 P-256/ES256,
  RSA ≥ 2048/RS256, OKP Ed25519/EdDSA), no trailing bytes unless the extensions flag says so.
- Authentication: the signature over authenticatorData || SHA-256(clientDataJSON) with the stored key,
  and the signature counter (when both the stored and the new value are non-zero, the new one must be
  larger, otherwise the credential may have been cloned: rejected and logged).

Attestation: we ask for `attestation: "none"` and don't verify attestation statements. Attestation only
proves which make/model of authenticator created the key; we accept any authenticator, synced passkeys
(iCloud Keychain, Google Password Manager, 1Password …) send no attestation at all, and verifying it would
need vendor root certificates and metadata. The security of a passkey comes from the challenge, origin
and RP ID binding checked above, and from the private key never leaving the authenticator.

A passkey belongs to one RP ID (domain): passkeys made on the dev app don't work on attnd.app.
"""

import base64
import hashlib
import hmac
import io
import json
import logging
import secrets
import struct
import time
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import cbor2
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, ed25519, padding, rsa
from django.conf import settings

logger = logging.getLogger(__name__)

CHALLENGE_TTL = 300  # seconds a challenge stays valid
TIMEOUT_MS = 120_000  # what the browser is told (it may show its own UI that long)

# COSE algorithm identifiers (RFC 9053 / IANA): ES256, EdDSA (Ed25519), RS256.
ES256, EDDSA, RS256 = -7, -8, -257
SUPPORTED_ALGS = (ES256, EDDSA, RS256)

FLAG_UP, FLAG_UV, FLAG_BE, FLAG_BS, FLAG_AT, FLAG_ED = 0x01, 0x04, 0x08, 0x10, 0x40, 0x80


class WebAuthnError(Exception):
    """The browser's response didn't verify. The message is for logs, not for people."""


# ---------------------------------------------------------------------------
# Encoding helpers
# ---------------------------------------------------------------------------

def b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode('ascii')


def b64url_decode(text) -> bytes:
    if not isinstance(text, str):
        raise WebAuthnError('expected a base64url string')
    try:
        return base64.urlsafe_b64decode(text + '=' * (-len(text) % 4))
    except (ValueError, TypeError) as exc:
        raise WebAuthnError('bad base64url') from exc


def new_challenge() -> bytes:
    return secrets.token_bytes(32)


def new_user_handle() -> bytes:
    """The WebAuthn user.id: random, not the database id or the email (it is stored on the authenticator)."""
    return secrets.token_bytes(32)


# ---------------------------------------------------------------------------
# Relying party (domain) settings
# ---------------------------------------------------------------------------

def rp_id(request=None) -> str:
    """settings.WEBAUTHN_RP_ID; local development (DEBUG, page served from localhost) uses "localhost"."""
    if request is not None and settings.DEBUG:
        host = request.get_host().rsplit(':', 1)[0].lower()
        if host == 'localhost':
            return 'localhost'
    return settings.WEBAUTHN_RP_ID


def allowed_origins() -> list[str]:
    return list(settings.WEBAUTHN_ORIGINS)


def _origin_matches_rp(origin: str, rp: str) -> bool:
    host = (urlsplit(origin).hostname or '').lower()
    return host == rp or host.endswith('.' + rp)


# ---------------------------------------------------------------------------
# Options for navigator.credentials.create() / .get() (JSON; binary values base64url)
# ---------------------------------------------------------------------------

def registration_options(*, rp: str, user_handle: bytes, user_name: str, display_name: str,
                         challenge: bytes, exclude: list[dict]) -> dict:
    return {
        'rp': {'id': rp, 'name': settings.WEBAUTHN_RP_NAME},
        'user': {'id': b64url_encode(user_handle), 'name': user_name, 'displayName': display_name},
        'challenge': b64url_encode(challenge),
        'pubKeyCredParams': [{'type': 'public-key', 'alg': alg} for alg in SUPPORTED_ALGS],
        'timeout': TIMEOUT_MS,
        'excludeCredentials': exclude,
        'authenticatorSelection': {'residentKey': 'required', 'requireResidentKey': True,
                                   'userVerification': 'required'},
        'attestation': 'none',
        'hints': ['client-device', 'hybrid'],
        'extensions': {'credProps': True},
    }


def authentication_options(*, rp: str, challenge: bytes, allow: list[dict]) -> dict:
    return {
        'rpId': rp,
        'challenge': b64url_encode(challenge),
        'timeout': TIMEOUT_MS,
        'allowCredentials': allow,  # empty: discoverable credentials (usernameless, autofill)
        'userVerification': 'required',
    }


def credential_descriptor(credential_id: str, transports=None) -> dict:
    descriptor = {'type': 'public-key', 'id': credential_id}
    if transports:
        descriptor['transports'] = list(transports)
    return descriptor


# ---------------------------------------------------------------------------
# Challenges in the session (a few outstanding per purpose, each used once)
# ---------------------------------------------------------------------------
# Several can be outstanding at once: the sign-in page's autofill request and the "Sign in with a passkey"
# button each get one, and so does a second tab. A response is matched to its own challenge, which is then
# removed whether or not the response verifies.

MAX_OUTSTANDING = 5


def store_challenge(session, purpose: str, challenge: bytes, **extra) -> None:
    key = f'_webauthn_{purpose}'
    now = time.time()
    pending = [c for c in session.get(key, []) if isinstance(c, dict) and c.get('expires', 0) >= now]
    pending.append({'challenge': b64url_encode(challenge), 'expires': now + CHALLENGE_TTL, **extra})
    session[key] = pending[-MAX_OUTSTANDING:]


def client_challenge(credential) -> str:
    """The challenge a response claims to answer (unverified; only used to find our stored one)."""
    try:
        client = json.loads(b64url_decode(_response(credential).get('clientDataJSON')).decode('utf-8'))
        return str(client.get('challenge', '')) if isinstance(client, dict) else ''
    except (UnicodeDecodeError, ValueError, WebAuthnError):
        return ''


def pop_challenge(session, purpose: str, challenge: str) -> dict | None:
    """The stored, unexpired challenge `challenge` (base64url) with its extras, removed from the session
    (bytes under 'challenge'); None if there is no such challenge."""
    key = f'_webauthn_{purpose}'
    pending = session.get(key, [])
    if not isinstance(pending, list) or not challenge:
        return None
    found = None
    for entry in pending:
        if isinstance(entry, dict) and hmac.compare_digest(str(entry.get('challenge', '')).encode(), challenge.encode()):
            found = entry
    if found is None:
        return None
    session[key] = [entry for entry in pending if entry is not found]
    if found.get('expires', 0) < time.time():
        return None
    return {**found, 'challenge': b64url_decode(found['challenge'])}


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

@dataclass
class AuthenticatorData:
    rp_id_hash: bytes
    flags: int
    sign_count: int
    aaguid: bytes = b''
    credential_id: bytes = b''
    public_key: bytes = b''  # COSE_Key, CBOR-encoded, as received
    extensions: dict = field(default_factory=dict)

    @property
    def user_present(self): return bool(self.flags & FLAG_UP)

    @property
    def user_verified(self): return bool(self.flags & FLAG_UV)

    @property
    def backup_eligible(self): return bool(self.flags & FLAG_BE)

    @property
    def backed_up(self): return bool(self.flags & FLAG_BS)


def parse_authenticator_data(data: bytes) -> AuthenticatorData:
    if len(data) < 37:
        raise WebAuthnError('authenticator data too short')
    rp_hash, flags, (count,) = data[:32], data[32], struct.unpack('>I', data[33:37])
    parsed = AuthenticatorData(rp_id_hash=rp_hash, flags=flags, sign_count=count)
    stream = io.BytesIO(data[37:])
    if flags & FLAG_AT:
        head = stream.read(18)
        if len(head) < 18:
            raise WebAuthnError('attested credential data too short')
        parsed.aaguid = head[:16]
        (id_len,) = struct.unpack('>H', head[16:18])
        if id_len > 1023:
            raise WebAuthnError('credential id too long')
        parsed.credential_id = stream.read(id_len)
        if len(parsed.credential_id) != id_len or not id_len:
            raise WebAuthnError('credential id truncated')
        start = stream.tell()
        try:
            key = cbor2.CBORDecoder(stream).decode()
        except Exception as exc:  # cbor2 raises several error types for malformed input
            raise WebAuthnError('bad credential public key') from exc
        if not isinstance(key, dict):
            raise WebAuthnError('credential public key is not a map')
        end = stream.tell()
        parsed.public_key = data[37 + start:37 + end]
    if flags & FLAG_ED:
        try:
            ext = cbor2.CBORDecoder(stream).decode()
        except Exception as exc:
            raise WebAuthnError('bad extensions') from exc
        parsed.extensions = ext if isinstance(ext, dict) else {}
    if stream.read(1):
        raise WebAuthnError('trailing bytes in authenticator data')
    if parsed.backed_up and not parsed.backup_eligible:
        raise WebAuthnError('backup state set without backup eligibility')
    return parsed


def load_public_key(cose_key: bytes):
    """(cryptography public key, COSE alg) from a CBOR COSE_Key; only the algorithms we offered."""
    try:
        key = cbor2.loads(cose_key)
    except Exception as exc:
        raise WebAuthnError('bad COSE key') from exc
    if not isinstance(key, dict):
        raise WebAuthnError('COSE key is not a map')
    kty, alg = key.get(1), key.get(3)
    try:
        if kty == 2 and alg == ES256:  # EC2
            x, y = key.get(-2), key.get(-3)
            if key.get(-1) != 1 or not isinstance(x, bytes) or not isinstance(y, bytes) or len(x) != 32 or len(y) != 32:
                raise WebAuthnError('unsupported EC2 key (P-256 only)')
            public = ec.EllipticCurvePublicNumbers(int.from_bytes(x, 'big'), int.from_bytes(y, 'big'),
                                                   ec.SECP256R1()).public_key()  # checks the point is on the curve
        elif kty == 3 and alg == RS256:  # RSA
            n, e = key.get(-1), key.get(-2)
            if not isinstance(n, bytes) or not isinstance(e, bytes):
                raise WebAuthnError('bad RSA key')
            public = rsa.RSAPublicNumbers(int.from_bytes(e, 'big'), int.from_bytes(n, 'big')).public_key()
            if public.key_size < 2048:
                raise WebAuthnError('RSA key too small')
        elif kty == 1 and alg == EDDSA:  # OKP
            x = key.get(-2)
            if key.get(-1) != 6 or not isinstance(x, bytes) or len(x) != 32:
                raise WebAuthnError('unsupported OKP key (Ed25519 only)')
            public = ed25519.Ed25519PublicKey.from_public_bytes(x)
        else:
            raise WebAuthnError(f'unsupported key type {kty!r} / algorithm {alg!r}')
    except ValueError as exc:
        raise WebAuthnError('invalid public key') from exc
    return public, alg


def verify_signature(cose_key: bytes, signature: bytes, data: bytes) -> None:
    public, alg = load_public_key(cose_key)
    try:
        if alg == ES256:
            public.verify(signature, data, ec.ECDSA(hashes.SHA256()))  # DER-encoded signature
        elif alg == RS256:
            public.verify(signature, data, padding.PKCS1v15(), hashes.SHA256())
        else:
            public.verify(signature, data)
    except InvalidSignature as exc:
        raise WebAuthnError('bad signature') from exc


def _client_data(raw: bytes, expected_type: str, challenge: bytes, origins: list[str], rp: str) -> dict:
    try:
        client = json.loads(raw.decode('utf-8'))
    except (UnicodeDecodeError, ValueError) as exc:
        raise WebAuthnError('bad clientDataJSON') from exc
    if not isinstance(client, dict):
        raise WebAuthnError('bad clientDataJSON')
    if client.get('type') != expected_type:
        raise WebAuthnError(f'wrong type {client.get("type")!r}')
    sent = client.get('challenge')
    if not isinstance(sent, str) or not hmac.compare_digest(sent.encode(), b64url_encode(challenge).encode()):
        raise WebAuthnError('wrong challenge')
    origin = client.get('origin')
    if origin not in origins or not _origin_matches_rp(origin, rp):
        raise WebAuthnError(f'wrong origin {origin!r}')
    if client.get('crossOrigin') is True:
        raise WebAuthnError('cross-origin request')
    return client


def _check_auth_data(auth: AuthenticatorData, rp: str) -> None:
    if not hmac.compare_digest(auth.rp_id_hash, hashlib.sha256(rp.encode()).digest()):
        raise WebAuthnError('wrong RP ID hash')
    if not auth.user_present:
        raise WebAuthnError('user not present')
    if not auth.user_verified:
        raise WebAuthnError('user not verified')


def _response(credential) -> dict:
    if not isinstance(credential, dict) or credential.get('type') != 'public-key':
        raise WebAuthnError('not a public-key credential')
    response = credential.get('response')
    if not isinstance(response, dict):
        raise WebAuthnError('missing response')
    return response


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------

@dataclass
class NewCredential:
    credential_id: str  # base64url
    public_key: bytes  # COSE
    sign_count: int
    transports: list
    backup_eligible: bool
    backed_up: bool
    aaguid: str  # hex


def verify_registration(credential: dict, *, challenge: bytes, rp: str, origins: list[str]) -> NewCredential:
    """Checks a PublicKeyCredential (JSON from the browser) made by navigator.credentials.create()."""
    response = _response(credential)
    _client_data(b64url_decode(response.get('clientDataJSON')), 'webauthn.create', challenge, origins, rp)
    try:
        attestation = cbor2.loads(b64url_decode(response.get('attestationObject')))
    except WebAuthnError:
        raise
    except Exception as exc:
        raise WebAuthnError('bad attestationObject') from exc
    if not isinstance(attestation, dict) or not isinstance(attestation.get('authData'), bytes):
        raise WebAuthnError('attestationObject without authData')
    # attestation.fmt / attStmt are deliberately not verified (attestation "none"; see the module docstring).
    auth = parse_authenticator_data(attestation['authData'])
    _check_auth_data(auth, rp)
    if not auth.credential_id:
        raise WebAuthnError('no attested credential data')
    load_public_key(auth.public_key)  # supported algorithm, valid key
    credential_id = b64url_encode(auth.credential_id)
    raw_id = credential.get('rawId') or credential.get('id')
    if raw_id is not None and b64url_decode(raw_id) != auth.credential_id:
        raise WebAuthnError('credential id mismatch')
    transports = response.get('transports') or []
    if not isinstance(transports, list):
        transports = []
    transports = [t for t in transports if isinstance(t, str) and len(t) <= 32][:8]
    return NewCredential(credential_id=credential_id, public_key=auth.public_key, sign_count=auth.sign_count,
                         transports=transports, backup_eligible=auth.backup_eligible, backed_up=auth.backed_up,
                         aaguid=auth.aaguid.hex())


@dataclass
class Assertion:
    credential_id: str  # base64url
    user_handle: bytes | None
    auth: AuthenticatorData


def parse_assertion(credential: dict) -> tuple[str, bytes | None]:
    """(credential id, user handle) from a navigator.credentials.get() response, before verification, so the
    caller can look up the stored passkey."""
    response = _response(credential)
    raw_id = credential.get('rawId') or credential.get('id')
    credential_id = b64url_encode(b64url_decode(raw_id))
    handle = response.get('userHandle')
    return credential_id, (b64url_decode(handle) if handle else None)


def verify_authentication(credential: dict, *, challenge: bytes, rp: str, origins: list[str],
                          public_key: bytes, stored_sign_count: int) -> AuthenticatorData:
    """Checks a navigator.credentials.get() response against the stored passkey. Returns the parsed
    authenticator data (new sign count, backup state)."""
    response = _response(credential)
    client_raw = b64url_decode(response.get('clientDataJSON'))
    _client_data(client_raw, 'webauthn.get', challenge, origins, rp)
    auth_raw = b64url_decode(response.get('authenticatorData'))
    auth = parse_authenticator_data(auth_raw)
    _check_auth_data(auth, rp)
    signature = b64url_decode(response.get('signature'))
    verify_signature(public_key, signature, auth_raw + hashlib.sha256(client_raw).digest())
    if auth.sign_count and stored_sign_count and auth.sign_count <= stored_sign_count:
        logger.warning('Passkey sign count went from %s to %s: possibly a cloned authenticator; rejected',
                       stored_sign_count, auth.sign_count)
        raise WebAuthnError('sign count did not increase')
    return auth


# ---------------------------------------------------------------------------
# Friendly default names
# ---------------------------------------------------------------------------

def default_name(user_agent: str) -> str:
    """"iPhone", "Mac", "Android phone" … from the User-Agent, for a new passkey's name."""
    ua = user_agent or ''
    for needle, name in (('iPhone', 'iPhone'), ('iPad', 'iPad'), ('Android', 'Android phone'),
                         ('CrOS', 'Chromebook'), ('Windows', 'Windows PC'), ('Macintosh', 'Mac'),
                         ('Mac OS X', 'Mac'), ('Linux', 'Linux computer')):
        if needle in ua:
            return name
    return 'Passkey'
