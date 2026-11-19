"""
Tests for the attestation app.

iOS attestation verification requires real Apple-signed certificates, so we
test the full verify_attestation() flow with mocks rather than live certs.
The nonce/token endpoint tests use Django's test client and hit the real views.
"""

import base64
import hashlib
import json
import secrets
import struct
import unittest
from datetime import timedelta
from unittest.mock import MagicMock, patch

from django.test import TestCase, Client, override_settings
from django.urls import reverse
from django.utils import timezone

from attestation import tokens
from attestation.models import AttestedDevice
from attestation.ios_attest import (
    AttestationError,
    _b64url_to_b64,
    _extract_nonce_from_extension,
)

# Disable SSL redirect so the test client can hit plain HTTP URLs
_no_ssl = override_settings(SECURE_SSL_REDIRECT=False)


# ---------------------------------------------------------------------------
# tokens.py
# ---------------------------------------------------------------------------

class TokenTests(TestCase):

    def test_issue_and_verify_roundtrip(self):
        token = tokens.issue(device_key='test-key-123', platform='ios')
        payload = tokens.verify(token)
        self.assertIsNotNone(payload)
        self.assertEqual(payload['sub'], 'test-key-123')
        self.assertEqual(payload['platform'], 'ios')
        self.assertEqual(payload['iss'], 'attnd')

    def test_tampered_token_rejected(self):
        token = tokens.issue(device_key='test-key', platform='android')
        tampered = token[:-4] + 'XXXX'
        self.assertIsNone(tokens.verify(tampered))

    def test_expired_token_rejected(self):
        with patch('attestation.tokens._TTL', timedelta(seconds=-1)):
            token = tokens.issue(device_key='old-key', platform='ios')
        self.assertIsNone(tokens.verify(token))

    def test_wrong_issuer_rejected(self):
        import jwt
        bad_token = jwt.encode(
            {'sub': 'k', 'platform': 'ios', 'iat': 0, 'exp': 9999999999, 'iss': 'other'},
            tokens._secret(),
            algorithm='HS256',
        )
        self.assertIsNone(tokens.verify(bad_token))


# ---------------------------------------------------------------------------
# Nonce endpoint
# ---------------------------------------------------------------------------

@_no_ssl
class NonceViewTests(TestCase):

    def test_get_returns_nonce(self):
        resp = self.client.get('/api/v1/attest/nonce/')
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn('nonce', data)
        self.assertIsInstance(data['nonce'], str)
        self.assertGreater(len(data['nonce']), 16)

    def test_each_nonce_is_unique(self):
        n1 = self.client.get('/api/v1/attest/nonce/').json()['nonce']
        n2 = self.client.get('/api/v1/attest/nonce/').json()['nonce']
        self.assertNotEqual(n1, n2)


# ---------------------------------------------------------------------------
# iOS attestation endpoint (mocked)
# ---------------------------------------------------------------------------

@_no_ssl
class IOSAttestViewTests(TestCase):

    def _get_nonce(self):
        return self.client.get('/api/v1/attest/nonce/').json()['nonce']

    def test_missing_fields_returns_400(self):
        resp = self.client.post(
            '/api/v1/attest/ios/',
            data=json.dumps({'key_id': 'abc'}),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 400)

    def test_invalid_nonce_returns_400(self):
        resp = self.client.post(
            '/api/v1/attest/ios/',
            data=json.dumps({
                'key_id': 'abc',
                'attestation': base64.b64encode(b'fake').decode(),
                'nonce': 'totally-made-up-nonce',
            }),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn('nonce', resp.json()['error'].lower())

    @override_settings(APPLE_APP_BUNDLE_ID='com.test.attnd', APPLE_TEAM_ID='TESTTEAMID')
    def test_nonce_cannot_be_reused(self):
        nonce = self._get_nonce()
        # Consume it once via the view (attestation fails, but nonce is gone)
        with patch('attestation.views.verify_attestation', side_effect=AttestationError('bad cert')):
            resp1 = self.client.post(
                '/api/v1/attest/ios/',
                data=json.dumps({'key_id': 'k', 'attestation': 'YQ==', 'nonce': nonce}),
                content_type='application/json',
            )
        self.assertEqual(resp1.status_code, 400)
        # Second attempt with same nonce must be rejected at nonce check, not attestation
        resp2 = self.client.post(
            '/api/v1/attest/ios/',
            data=json.dumps({'key_id': 'k', 'attestation': 'YQ==', 'nonce': nonce}),
            content_type='application/json',
        )
        self.assertEqual(resp2.status_code, 400)
        self.assertIn('nonce', resp2.json()['error'].lower())

    @override_settings(APPLE_APP_BUNDLE_ID='com.test.attnd', APPLE_TEAM_ID='TESTTEAMID')
    def test_successful_attestation_creates_device_and_returns_token(self):
        from attestation.ios_attest import AttestationResult
        nonce = self._get_nonce()
        mock_result = AttestationResult(
            key_id='test-key-id',
            public_key_pem='-----BEGIN PUBLIC KEY-----\nfake\n-----END PUBLIC KEY-----\n',
        )
        with patch('attestation.views.verify_attestation', return_value=mock_result):
            resp = self.client.post(
                '/api/v1/attest/ios/',
                data=json.dumps({
                    'key_id': 'test-key-id',
                    'attestation': base64.b64encode(b'fake').decode(),
                    'nonce': nonce,
                }),
                content_type='application/json',
            )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn('token', data)
        # Token must be verifiable
        payload = tokens.verify(data['token'])
        self.assertIsNotNone(payload)
        self.assertEqual(payload['sub'], 'test-key-id')
        # Device must be stored in the database
        device = AttestedDevice.objects.get(device_key='test-key-id')
        self.assertEqual(device.platform, AttestedDevice.PLATFORM_IOS)


# ---------------------------------------------------------------------------
# Android attestation endpoint (mocked)
# ---------------------------------------------------------------------------

@_no_ssl
class AndroidAttestViewTests(TestCase):

    def _get_nonce(self):
        return self.client.get('/api/v1/attest/nonce/').json()['nonce']

    def test_missing_fields_returns_400(self):
        resp = self.client.post(
            '/api/v1/attest/android/',
            data=json.dumps({'device_id': 'abc'}),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 400)

    def test_successful_attestation_creates_device_and_returns_token(self):
        nonce = self._get_nonce()
        mock_result = {
            'verdict': {},
            'device_verdict': ['MEETS_DEVICE_INTEGRITY'],
            'app_verdict': 'PLAY_RECOGNIZED',
        }
        with patch('attestation.views.verify_integrity_token', return_value=mock_result):
            resp = self.client.post(
                '/api/v1/attest/android/',
                data=json.dumps({
                    'device_id': 'android-device-uuid',
                    'token': 'fake-integrity-token',
                    'nonce': nonce,
                }),
                content_type='application/json',
            )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn('token', data)
        device = AttestedDevice.objects.get(device_key='android-device-uuid')
        self.assertEqual(device.platform, AttestedDevice.PLATFORM_ANDROID)
        self.assertEqual(device.android_verdict, 'MEETS_DEVICE_INTEGRITY')


# ---------------------------------------------------------------------------
# ApiKeyRequired mixin (in events/apis.py)
# ---------------------------------------------------------------------------

@_no_ssl
@override_settings(API_KEY='test-static-api-key')
class ApiKeyAuthTests(TestCase):
    """
    Ensure that both a static API key and an attestation JWT are accepted,
    and that invalid credentials are rejected.
    Uses the event lookup endpoint as a convenient auth-gated URL.
    """

    def _auth_header(self, value):
        return {'HTTP_AUTHORIZATION': f'Bearer {value}'}

    def test_static_api_key_accepted(self):
        resp = self.client.get(
            '/api/v2/events/lookup/?q=test',
            **self._auth_header('test-static-api-key'),
        )
        # 404 is fine — we just want to confirm it passed auth
        self.assertNotEqual(resp.status_code, 401)

    def test_attestation_jwt_accepted(self):
        token = tokens.issue(device_key='device-abc', platform='ios')
        resp = self.client.get(
            '/api/v2/events/lookup/?q=test',
            **self._auth_header(token),
        )
        self.assertNotEqual(resp.status_code, 401)

    def test_no_auth_rejected(self):
        resp = self.client.get('/api/v2/events/lookup/?q=test')
        self.assertEqual(resp.status_code, 401)

    def test_wrong_key_rejected(self):
        resp = self.client.get(
            '/api/v2/events/lookup/?q=test',
            **self._auth_header('wrong-key'),
        )
        self.assertEqual(resp.status_code, 401)


# ---------------------------------------------------------------------------
# ios_attest helper unit tests (no network, no real certs)
# ---------------------------------------------------------------------------

class B64UrlConversionTests(unittest.TestCase):

    def test_url_safe_chars_converted(self):
        self.assertEqual(_b64url_to_b64('abc-def_ghi'), 'abc+def/ghi')

    def test_plain_b64_unchanged(self):
        self.assertEqual(_b64url_to_b64('abcABC123+/'), 'abcABC123+/')


class ExtractNonceTests(unittest.TestCase):

    def _build_der_nonce(self, nonce_bytes: bytes) -> bytes:
        """
        Build a minimal DER structure:
          SEQUENCE { [1] EXPLICIT { OCTET STRING { nonce } } }
        """
        octet = b'\x04' + bytes([len(nonce_bytes)]) + nonce_bytes
        explicit = b'\xa1' + bytes([len(octet)]) + octet
        seq = b'\x30' + bytes([len(explicit)]) + explicit
        return seq

    def test_extracts_nonce_from_der(self):
        nonce = b'\xde\xad\xbe\xef' * 8  # 32 bytes
        raw = self._build_der_nonce(nonce)
        result = _extract_nonce_from_extension(raw)
        self.assertEqual(result, nonce)

    def test_raises_on_empty_input(self):
        from attestation.ios_attest import AttestationError
        with self.assertRaises(AttestationError):
            _extract_nonce_from_extension(b'')


# ---------------------------------------------------------------------------
# Play Integrity verdict policy + DB nonces + static key switch
# ---------------------------------------------------------------------------

import time as _time  # noqa: E402

from django.test import override_settings  # noqa: E402

from attestation.android_attest import AndroidAttestationError, check_verdict  # noqa: E402
from attestation.models import AttestationNonce  # noqa: E402


def _verdict(**overrides):
    payload = {
        'requestDetails': {'requestPackageName': 'com.arcu.attnd', 'nonce': 'abc_DEF-123',
                           'timestampMillis': str(int(_time.time() * 1000))},
        'appIntegrity': {'appRecognitionVerdict': 'PLAY_RECOGNIZED', 'packageName': 'com.arcu.attnd',
                         'certificateSha256Digest': ['qrvM3e7_AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBk']},
        'deviceIntegrity': {'deviceRecognitionVerdict': ['MEETS_DEVICE_INTEGRITY']},
        'accountDetails': {'appLicensingVerdict': 'LICENSED'},
    }
    for path, value in overrides.items():
        section, key = path.split('__')
        payload[section][key] = value
    return payload


class PlayIntegrityPolicyTests(TestCase):
    def check(self, payload, **kw):
        return check_verdict(payload, nonce='abc_DEF-123', package_name='com.arcu.attnd', **kw)

    def test_official_app_on_genuine_device_passes(self):
        self.assertEqual(self.check(_verdict())['app_verdict'], 'PLAY_RECOGNIZED')

    def test_nonce_is_compared_as_sent(self):
        with self.assertRaises(AndroidAttestationError):
            self.check(_verdict(requestDetails__nonce='YWJjX0RFRi0xMjM='))  # base64 of the nonce ≠ nonce

    def test_modified_or_sideloaded_build_rejected(self):
        for verdict in ('UNRECOGNIZED_VERSION', 'UNEVALUATED'):
            with self.subTest(verdict=verdict), self.assertRaises(AndroidAttestationError):
                self.check(_verdict(appIntegrity__appRecognitionVerdict=verdict))

    @override_settings(ANDROID_ALLOW_UNRECOGNIZED_APP=True)
    def test_unrecognized_allowed_only_when_configured(self):
        self.check(_verdict(appIntegrity__appRecognitionVerdict='UNRECOGNIZED_VERSION'))

    def test_rooted_or_emulated_device_rejected(self):
        for labels in ([], ['MEETS_BASIC_INTEGRITY'], ['MEETS_VIRTUAL_INTEGRITY']):
            with self.subTest(labels=labels), self.assertRaises(AndroidAttestationError):
                self.check(_verdict(deviceIntegrity__deviceRecognitionVerdict=labels))

    def test_not_installed_from_play_rejected(self):
        with self.assertRaises(AndroidAttestationError):
            self.check(_verdict(accountDetails__appLicensingVerdict='UNLICENSED'))

    def test_stale_token_rejected(self):
        old = str(int(_time.time() * 1000) - 60 * 60 * 1000)
        with self.assertRaises(AndroidAttestationError):
            self.check(_verdict(requestDetails__timestampMillis=old))

    def test_signing_certificate_pin(self):
        # 32 bytes AA BB CC DD EE FF 00 01 ... in colon-hex form, as Play Console shows it
        hex_digest = ':'.join(f'{b:02X}' for b in bytes([0xAA, 0xBB, 0xCC, 0xDD, 0xEE, 0xFF]) + bytes(range(26)))
        with override_settings(ANDROID_SIGNING_CERT_SHA256=hex_digest):
            self.check(_verdict())
        with override_settings(ANDROID_SIGNING_CERT_SHA256='11:22:33'):
            with self.assertRaises(AndroidAttestationError):
                self.check(_verdict())


class DatabaseNonceTests(TestCase):
    def test_nonce_is_stored_and_single_use(self):
        from attestation.views import _consume_nonce
        nonce = self.client.get('/api/v1/attest/nonce/').json()['nonce']
        self.assertTrue(AttestationNonce.objects.filter(nonce=nonce).exists())
        self.assertTrue(_consume_nonce(nonce))
        self.assertFalse(_consume_nonce(nonce))

    def test_expired_nonce_rejected_and_purged(self):
        from datetime import timedelta
        from django.utils import timezone
        from attestation.views import _consume_nonce, purge_expired_nonces
        AttestationNonce.objects.create(nonce='old', created_at=timezone.now() - timedelta(minutes=10))
        self.assertFalse(_consume_nonce('old'))
        AttestationNonce.objects.create(nonce='old2', created_at=timezone.now() - timedelta(minutes=10))
        self.assertEqual(purge_expired_nonces(), 2)  # both expired nonces

    def test_nonce_endpoint_rate_limited(self):
        from django.core.cache import cache
        cache.clear()
        codes = [self.client.get('/api/v1/attest/nonce/').status_code for _ in range(31)]
        self.assertEqual(codes[-1], 429)
        cache.clear()


@override_settings(API_KEY='static-key')
class StaticKeySwitchTests(TestCase):
    def test_static_key_can_be_disabled(self):
        url = '/api/v2/events/lookup/?q=nothing'
        self.assertEqual(self.client.get(url, HTTP_AUTHORIZATION='Bearer static-key').status_code, 404)
        with override_settings(STATIC_API_KEY_ENABLED=False):
            self.assertEqual(self.client.get(url, HTTP_AUTHORIZATION='Bearer static-key').status_code, 401)
            token = tokens.issue(device_key='dev', platform='ios')
            self.assertEqual(self.client.get(url, HTTP_AUTHORIZATION=f'Bearer {token}').status_code, 404)


class AppClipBundleTests(TestCase):
    def test_clip_bundle_id_matches(self):
        import hashlib
        from attestation.ios_attest import _matching_app_id
        team = 'GQQX4L6TE2'
        clip = hashlib.sha256(f'{team}.com.arcu.Attnd.clip'.encode()).digest()
        other = hashlib.sha256(f'{team}.com.evil.app'.encode()).digest()
        self.assertEqual(_matching_app_id(clip, team, 'com.arcu.Attnd'), f'{team}.com.arcu.Attnd.clip')
        self.assertIsNone(_matching_app_id(other, team, 'com.arcu.Attnd'))


class AppleRootCertificateTests(unittest.TestCase):
    """The embedded Apple App Attestation Root CA must be the real, intact certificate."""

    def test_root_loads_and_is_self_signed(self):
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import ec

        from attestation.ios_attest import _APPLE_ROOT_CA_PEM

        cert = x509.load_pem_x509_certificate(_APPLE_ROOT_CA_PEM)
        self.assertIn('CN=Apple App Attestation Root CA', cert.subject.rfc4514_string())
        cert.public_key().verify(cert.signature, cert.tbs_certificate_bytes, ec.ECDSA(cert.signature_hash_algorithm))
        self.assertEqual(
            cert.fingerprint(hashes.SHA256()).hex(),
            '1cb9823ba28ba6ad2d33a006941de2ae4f513ef1d4e831b9f7e0fa7b6242c932')


class CertChainTests(unittest.TestCase):
    """A chain shaped like Apple's: a P-384 root signing with SHA384, an intermediate signing with SHA256."""

    @staticmethod
    def _cert(subject, issuer, key, issuer_key, algorithm, ca, days=365):
        from datetime import datetime, timedelta, timezone

        from cryptography import x509 as cx509
        from cryptography.x509.oid import NameOID
        now = datetime.now(timezone.utc)
        return (cx509.CertificateBuilder()
                .subject_name(cx509.Name([cx509.NameAttribute(NameOID.COMMON_NAME, subject)]))
                .issuer_name(cx509.Name([cx509.NameAttribute(NameOID.COMMON_NAME, issuer)]))
                .public_key(key.public_key()).serial_number(cx509.random_serial_number())
                .not_valid_before(now - timedelta(days=1)).not_valid_after(now + timedelta(days=days))
                .add_extension(cx509.BasicConstraints(ca=ca, path_length=None), critical=True)
                .sign(issuer_key, algorithm))

    def setUp(self):
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import ec
        root_key, int_key, leaf_key = ec.generate_private_key(ec.SECP384R1()), ec.generate_private_key(ec.SECP384R1()), ec.generate_private_key(ec.SECP256R1())
        self.root = self._cert('Root', 'Root', root_key, root_key, hashes.SHA384(), True)
        self.intermediate = self._cert('Intermediate', 'Root', int_key, root_key, hashes.SHA384(), True)
        self.leaf = self._cert('Leaf', 'Intermediate', leaf_key, int_key, hashes.SHA256(), False)
        self.other_root_key = ec.generate_private_key(ec.SECP384R1())

    def test_mixed_hash_chain_verifies(self):
        from attestation.ios_attest import _verify_cert_chain
        _verify_cert_chain(self.leaf, self.intermediate, self.root)

    def test_wrong_root_and_expired_are_rejected(self):
        from datetime import datetime, timedelta, timezone

        from cryptography.hazmat.primitives import hashes

        from attestation.ios_attest import AttestationError, _verify_cert_chain
        impostor = self._cert('Root', 'Root', self.other_root_key, self.other_root_key, hashes.SHA384(), True)
        with self.assertRaises(AttestationError):
            _verify_cert_chain(self.leaf, self.intermediate, impostor)
        with self.assertRaises(AttestationError):
            _verify_cert_chain(self.leaf, self.intermediate, self.root, now=datetime.now(timezone.utc) + timedelta(days=400))


class KeyIdTests(unittest.TestCase):
    def test_key_id_hashes_the_raw_point(self):
        import base64
        import hashlib

        from cryptography.hazmat.primitives.asymmetric import ec

        from attestation.ios_attest import key_id_for
        key = ec.generate_private_key(ec.SECP256R1()).public_key()
        nums = key.public_numbers()
        point = b'\x04' + nums.x.to_bytes(32, 'big') + nums.y.to_bytes(32, 'big')
        self.assertEqual(key_id_for(key), base64.b64encode(hashlib.sha256(point).digest()).decode())


class PlayIntegrityRequestTests(TestCase):
    """verify_integrity_token's call to Google (httpx), with Google's answer mocked."""

    def _call(self, response):
        import httpx
        from attestation import android_attest
        response.request = httpx.Request('POST', 'https://playintegrity.googleapis.com/')
        with patch.object(android_attest, '_get_google_credentials', return_value='google-token'), \
                patch('attestation.android_attest.httpx.post', return_value=response) as post, \
                patch.object(android_attest, 'check_verdict', side_effect=lambda payload, **kw: payload):
            result = android_attest.verify_integrity_token(token='tok', nonce='n', package_name='com.arcu.attnd')
        return result, post

    def test_decodes_the_token_with_a_bearer_token(self):
        import httpx
        result, post = self._call(httpx.Response(200, json={'tokenPayloadExternal': {'ok': True}}))
        self.assertEqual(result, {'ok': True})
        self.assertEqual(post.call_args.kwargs['json'], {'integrity_token': 'tok'})
        self.assertEqual(post.call_args.kwargs['headers']['Authorization'], 'Bearer google-token')
        self.assertIn('com.arcu.attnd:decodeIntegrityToken', post.call_args.args[0])

    def test_http_errors_become_attestation_errors(self):
        import httpx
        with self.assertRaisesRegex(AndroidAttestationError, '403'):
            self._call(httpx.Response(403, text='denied'))
