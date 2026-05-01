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
        from django.conf import settings
        bad_token = jwt.encode(
            {'sub': 'k', 'platform': 'ios', 'iat': 0, 'exp': 9999999999, 'iss': 'other'},
            settings.SECRET_KEY,
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
            '/api/v1/events/lookup/?q=test',
            **self._auth_header('test-static-api-key'),
        )
        # 404 is fine — we just want to confirm it passed auth
        self.assertNotEqual(resp.status_code, 401)

    def test_attestation_jwt_accepted(self):
        token = tokens.issue(device_key='device-abc', platform='ios')
        resp = self.client.get(
            '/api/v1/events/lookup/?q=test',
            **self._auth_header(token),
        )
        self.assertNotEqual(resp.status_code, 401)

    def test_no_auth_rejected(self):
        resp = self.client.get('/api/v1/events/lookup/?q=test')
        self.assertEqual(resp.status_code, 401)

    def test_wrong_key_rejected(self):
        resp = self.client.get(
            '/api/v1/events/lookup/?q=test',
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
