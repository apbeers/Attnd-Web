"""
Apple App Attest verification (attestation + assertion).

References:
  https://developer.apple.com/documentation/devicecheck/validating-apps-that-connect-to-your-server
  https://www.w3.org/TR/webauthn-2/#sctn-attestation

Flow
----
1. Client calls GET /api/v1/attest/nonce/ to get a server-generated challenge.
2. Client calls DCAppAttestService.generateKey() → key_id
3. Client calls DCAppAttestService.attestKey(key_id, clientDataHash=SHA256(challenge))
   → base64-encoded CBOR attestation object.
4. Client POSTs {key_id, attestation, challenge} to /api/v1/attest/ios/.
5. This module verifies the attestation and returns the device's public key.

After attestation, for each API call:
6. Client builds clientData = {challenge, bundle_id, ...} and signs it with DCAppAttestService.generateAssertion()
7. Client sends {assertion, clientData} along with the request.
8. Backend verifies with verify_assertion() using the stored public key.

Attestation object (CBOR) structure
------------------------------------
{
    "fmt":      "apple-appattest",
    "attStmt":  {"x5c": [<leaf_cert_der>, <int_cert_der>], "receipt": <bytes>},
    "authData": <bytes>    # WebAuthn authenticator data
}

authenticatorData layout
-------------------------
 0..31   rpIdHash        SHA-256(bundle_id)
32       flags           (not checked here)
33..36   signCount       big-endian uint32 — MUST be 0 at attestation time
37..52   aaguid          16 bytes
53..54   credIdLen       big-endian uint16
55..     credentialId    raw bytes (= key_id decoded from base64url)
"""

import base64
import hashlib
import logging
import struct
from dataclasses import dataclass

import cbor2
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.ec import ECDSA
from cryptography.x509.oid import ExtensionOID

logger = logging.getLogger(__name__)

# Apple App Attest Root CA (DER, base64-encoded)
# Source: https://www.apple.com/certificateauthority/Apple_App_Attestation_Root_CA.pem
_APPLE_ROOT_CA_PEM = b"""
-----BEGIN CERTIFICATE-----
MIICITCCAaegAwIBAgIQC/O+DvHN0uD7jG5yH2IXmDAKBggqhkjOPQQDAzBSMSYw
JAYDVQQDDB1BcHBsZSBBcHAgQXR0ZXN0YXRpb24gUm9vdCBDQTETMBEGA1UECgwK
QXBwbGUgSW5jLjETMBEGA1UECAwKQ2FsaWZvcm5pYTAeFw0yMDAzMTgxODMyNTNa
Fw00NTAzMTUwMDAwMDBaMFIxJjAkBgNVBAMMHUFwcGxlIEFwcCBBdHRlc3RhdGlv
biBSb290IENBMRMwEQYDVQQKDApBcHBsZSBJbmMuMRMwEQYDVQQIDApDYWxpZm9y
bmlhMHYwEAYHKoZIzj0CAQYFK4EEACIDYgAERTHhmLW07ATaFQIEVwTbQlEU0i+m
PSTqncom6vPBZSFHBSHkSHFdKL1K4Kxkm8cJmPH6RVXG1JLdEkEAp0Nx4LL5xX3
BKRM67C5JQi2p7XFZMXTKBkfBGJM/RCOo2YwZDASBgNVHRMBAf8ECDAGAQH/AgEA
MB8GA1UdIwQYMBaAFKyREFMzvb5oRf+nDKnl+url5YqhMB0GA1UdDgQWBBSskRBT
M72+aEX/pwyp5frq5eWKoTAOBgNVHQ8BAf8EBAMCAQYwCgYIKoZIzj0EAwMDaAAw
ZQIwQgFGliNR2oR4I9Gb/9sMi0pZqJTfMNIPdLNBFjyDBXP+jb1dYcGfzGkfCR7E
BlSEAjEAqJqQMkNZ4OuR17xWDzRHCY7PgWbRELblCxkdP0DK+uf0yK3q+5bHy+KB
PBSV
-----END CERTIFICATE-----
""".strip()

# Apple's OID for the nonce embedded in the credential certificate
_NONCE_OID = x509.ObjectIdentifier('1.2.840.113635.100.8.2')

# aaguid values
_AAGUID_PROD = b'appattest' + b'\x00' * 7   # production
_AAGUID_DEV  = b'appattestdevelop'            # development / sandbox


class AttestationError(Exception):
    """Raised when attestation verification fails at any step."""


@dataclass
class AttestationResult:
    key_id: str          # base64url key identifier
    public_key_pem: str  # PEM-encoded EC public key from the credential cert


def verify_attestation(
    *,
    key_id: str,
    attestation_b64: str,
    challenge: str,
    bundle_id: str,
    team_id: str,
    environment: str = 'production',  # 'production' | 'development'
) -> AttestationResult:
    """
    Full Apple App Attest attestation verification per Apple's documentation.

    Raises AttestationError with a descriptive message on any failure.
    Returns AttestationResult on success.
    """
    # Step 0 — decode inputs
    try:
        attestation_bytes = base64.b64decode(attestation_b64 + '==')
    except Exception as exc:
        raise AttestationError(f"Cannot base64-decode attestation: {exc}") from exc

    try:
        obj = cbor2.loads(attestation_bytes)
    except Exception as exc:
        raise AttestationError(f"Cannot CBOR-decode attestation: {exc}") from exc

    fmt = obj.get('fmt')
    if fmt != 'apple-appattest':
        raise AttestationError(f"Unexpected fmt: {fmt!r}")

    att_stmt = obj.get('attStmt', {})
    auth_data: bytes = obj.get('authData', b'')
    x5c: list[bytes] = att_stmt.get('x5c', [])

    if len(x5c) < 2:
        raise AttestationError("x5c must contain at least leaf + intermediate")

    # Step 1 — parse and verify the certificate chain
    try:
        cred_cert = x509.load_der_x509_certificate(x5c[0])
        int_cert  = x509.load_der_x509_certificate(x5c[1])
        root_cert = x509.load_pem_x509_certificate(_APPLE_ROOT_CA_PEM)
    except Exception as exc:
        raise AttestationError(f"Cannot parse certificates: {exc}") from exc

    _verify_cert_chain(cred_cert, int_cert, root_cert)

    # Step 2 — compute nonce
    # nonce = SHA-256(authData || SHA-256(challenge_bytes))
    client_data_hash = hashlib.sha256(challenge.encode()).digest()
    composite = auth_data + client_data_hash
    expected_nonce = hashlib.sha256(composite).digest()

    # Step 3 — verify nonce is in the credential cert extension
    try:
        ext = cred_cert.extensions.get_extension_for_oid(_NONCE_OID)
        # The extension value is a DER ASN.1 sequence containing an octet string.
        # cbor2 can't help here — use raw ASN.1 extraction.
        cert_nonce = _extract_nonce_from_extension(ext.value.value)
    except x509.ExtensionNotFound:
        raise AttestationError("Credential cert missing nonce extension (OID 1.2.840.113635.100.8.2)")
    except Exception as exc:
        raise AttestationError(f"Cannot parse nonce extension: {exc}") from exc

    if cert_nonce != expected_nonce:
        raise AttestationError("Nonce mismatch — challenge has been tampered with or replayed")

    # Step 4 — verify public key hash matches key_id
    pub_key_der = cred_cert.public_key().public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    pub_key_hash_b64 = base64.b64encode(hashlib.sha256(pub_key_der).digest()).decode()
    if pub_key_hash_b64 != key_id and _b64url_to_b64(key_id) != pub_key_hash_b64:
        raise AttestationError(
            f"key_id mismatch: got {key_id!r}, expected hash of cert public key"
        )

    # Step 5 — parse authenticatorData
    if len(auth_data) < 55:
        raise AttestationError("authData too short")

    rp_id_hash = auth_data[0:32]
    sign_count  = struct.unpack('>I', auth_data[33:37])[0]
    aaguid      = auth_data[37:53]
    cred_id_len = struct.unpack('>H', auth_data[53:55])[0]
    cred_id     = auth_data[55:55 + cred_id_len]

    # Step 6 — verify rpIdHash = SHA-256("<team_id>.<bundle_id>")
    app_id = f"{team_id}.{bundle_id}"
    expected_rp_id_hash = hashlib.sha256(app_id.encode()).digest()
    if rp_id_hash != expected_rp_id_hash:
        raise AttestationError(
            f"rpIdHash mismatch — expected SHA-256({app_id!r})"
        )

    # Step 7 — counter must be 0 at attestation time
    if sign_count != 0:
        raise AttestationError(f"sign_count must be 0 at attestation, got {sign_count}")

    # Step 8 — verify aaguid matches environment
    if environment == 'production':
        if aaguid != _AAGUID_PROD:
            raise AttestationError(
                f"aaguid mismatch for production environment: {aaguid!r}"
            )
    else:
        if aaguid not in (_AAGUID_DEV, _AAGUID_PROD):
            raise AttestationError(f"Unrecognised aaguid: {aaguid!r}")

    # Step 9 — credentialId must match key_id
    expected_cred_id = base64.b64decode(_b64url_to_b64(key_id) + '==')
    if cred_id != expected_cred_id:
        raise AttestationError("credentialId in authData does not match key_id")

    public_key_pem = cred_cert.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()

    logger.info("iOS App Attest: verified key_id=%s app=%s", key_id[:16], app_id)
    return AttestationResult(key_id=key_id, public_key_pem=public_key_pem)


def verify_assertion(
    *,
    assertion_b64: str,
    client_data: bytes,
    stored_public_key_pem: str,
    stored_sign_count: int,
    bundle_id: str,
    team_id: str,
) -> int:
    """
    Verify a DCAppAttestService assertion (for ongoing API calls after initial attestation).

    Returns the new sign_count to persist.
    Raises AssertionError on any failure.
    """
    try:
        assertion_bytes = base64.b64decode(assertion_b64 + '==')
        obj = cbor2.loads(assertion_bytes)
    except Exception as exc:
        raise AttestationError(f"Cannot decode assertion: {exc}") from exc

    signature: bytes = obj.get('signature', b'')
    auth_data: bytes = obj.get('authenticatorData', b'')

    if len(auth_data) < 37:
        raise AttestationError("authenticatorData too short in assertion")

    # Verify rpIdHash
    rp_id_hash = auth_data[0:32]
    app_id = f"{team_id}.{bundle_id}"
    if rp_id_hash != hashlib.sha256(app_id.encode()).digest():
        raise AttestationError("rpIdHash mismatch in assertion")

    # Sign count must be strictly greater than stored value
    sign_count = struct.unpack('>I', auth_data[33:37])[0]
    if sign_count <= stored_sign_count:
        raise AttestationError(
            f"sign_count replay: stored={stored_sign_count}, received={sign_count}"
        )

    # Verify signature over authData || SHA-256(clientData)
    client_data_hash = hashlib.sha256(client_data).digest()
    signed_data = auth_data + client_data_hash

    try:
        from cryptography.hazmat.primitives.serialization import load_pem_public_key
        pub_key = load_pem_public_key(stored_public_key_pem.encode())
        pub_key.verify(signature, signed_data, ECDSA(hashes.SHA256()))
    except Exception as exc:
        raise AttestationError(f"Assertion signature verification failed: {exc}") from exc

    logger.debug("iOS assertion verified, new sign_count=%d", sign_count)
    return sign_count


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _verify_cert_chain(leaf: x509.Certificate, intermediate: x509.Certificate, root: x509.Certificate):
    """Verify leaf → intermediate → root chain using signature checks."""
    try:
        # intermediate signed by root
        root.public_key().verify(
            intermediate.signature,
            intermediate.tbs_certificate_bytes,
            ECDSA(hashes.SHA256()),
        )
        # leaf signed by intermediate
        intermediate.public_key().verify(
            leaf.signature,
            leaf.tbs_certificate_bytes,
            ECDSA(hashes.SHA256()),
        )
    except Exception as exc:
        raise AttestationError(f"Certificate chain verification failed: {exc}") from exc


def _extract_nonce_from_extension(raw: bytes) -> bytes:
    """
    The nonce extension value is DER-encoded:
        SEQUENCE { SEQUENCE { [1] EXPLICIT OCTET STRING { <32-byte nonce> } } }

    Walk the DER manually — no need for full ASN.1 parser.
    """
    # Strip outer SEQUENCE tags until we find an OCTET STRING (tag 0x04)
    pos = 0
    while pos < len(raw):
        tag = raw[pos]
        pos += 1
        # Read length
        if raw[pos] & 0x80:
            n_len_bytes = raw[pos] & 0x7F
            pos += 1
            length = int.from_bytes(raw[pos:pos + n_len_bytes], 'big')
            pos += n_len_bytes
        else:
            length = raw[pos]
            pos += 1

        if tag == 0x04:  # OCTET STRING
            return raw[pos:pos + length]
        # For SEQUENCE (0x30) or CONTEXT (0xA1 etc.) — recurse into the content
        elif tag in (0x30, 0xA1, 0xA0):
            result = _extract_nonce_from_extension(raw[pos:pos + length])
            if result:
                return result
        pos += length

    raise AttestationError("Could not find OCTET STRING in nonce extension")


def _b64url_to_b64(s: str) -> str:
    """Convert base64url to standard base64."""
    return s.replace('-', '+').replace('_', '/')
