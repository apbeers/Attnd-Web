"""Passkeys (WebAuthn), emailed codes and the sign-in policy around them.

A software authenticator (keys from `cryptography`, CBOR from cbor2) plays the browser + authenticator:
it builds clientDataJSON, authenticatorData, COSE keys and attestation objects and signs assertions,
so every check in console/webauthn.py runs against real signatures.
"""

import hashlib
import json
import os
import struct
from datetime import timedelta
from unittest import mock

import cbor2
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, ed25519, padding, rsa
from django.conf import settings
from django.contrib.auth.models import User
from django.core import mail
from django.core.cache import cache
from django.test import Client, SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from console import totp, twofactor, webauthn
from console.models import Passkey, SignInSecurity, TwoFactorDevice
from console.webauthn import FLAG_AT, FLAG_BE, FLAG_BS, FLAG_ED, FLAG_UP, FLAG_UV, WebAuthnError, b64url_encode

RP = 'attnd.app'
ORIGIN = 'https://attnd.app'
NOW = 1_800_000_000.0


def limits(**overrides):
    return override_settings(RATE_LIMITS={**settings.RATE_LIMITS, **overrides})


class SoftAuthenticator:
    """A passkey provider in software. `alg` is 'es256', 'eddsa' or 'rs256'."""

    def __init__(self, alg='es256', rp_id=RP, origin=ORIGIN, backup=True):
        self.alg, self.rp_id, self.origin = alg, rp_id, origin
        if alg == 'es256':
            self.key = ec.generate_private_key(ec.SECP256R1())
        elif alg == 'eddsa':
            self.key = ed25519.Ed25519PrivateKey.generate()
        else:
            self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.credential_id = os.urandom(32)
        self.user_handle = None
        self.sign_count = 0
        self.counts = False  # synced passkeys send 0; set True to count up
        self.backup = backup

    # --- building blocks -------------------------------------------------------------------------
    def cose_key(self) -> bytes:
        public = self.key.public_key()
        if self.alg == 'es256':
            n = public.public_numbers()
            return cbor2.dumps({1: 2, 3: -7, -1: 1, -2: n.x.to_bytes(32, 'big'), -3: n.y.to_bytes(32, 'big')})
        if self.alg == 'eddsa':
            from cryptography.hazmat.primitives import serialization
            raw = public.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
            return cbor2.dumps({1: 1, 3: -8, -1: 6, -2: raw})
        n = public.public_numbers()
        return cbor2.dumps({1: 3, 3: -257, -1: n.n.to_bytes(256, 'big'), -2: n.e.to_bytes(3, 'big')})

    def flags(self, up=True, uv=True):
        value = (FLAG_UP if up else 0) | (FLAG_UV if uv else 0)
        if self.backup:
            value |= FLAG_BE | FLAG_BS
        return value

    def auth_data(self, flags, attested=False, rp_id=None, count=None, extra=b''):
        rp_hash = hashlib.sha256((rp_id or self.rp_id).encode()).digest()
        if attested:
            flags |= FLAG_AT
        data = rp_hash + bytes([flags]) + struct.pack('>I', self.sign_count if count is None else count)
        if attested:
            data += b'\0' * 16 + struct.pack('>H', len(self.credential_id)) + self.credential_id + self.cose_key()
        return data + extra

    def client_data(self, kind, challenge, origin=None, **extra):
        return json.dumps({'type': kind, 'challenge': challenge, 'origin': origin or self.origin,
                           'crossOrigin': False, **extra}).encode()

    def sign(self, data: bytes) -> bytes:
        if self.alg == 'es256':
            return self.key.sign(data, ec.ECDSA(hashes.SHA256()))
        if self.alg == 'eddsa':
            return self.key.sign(data)
        return self.key.sign(data, padding.PKCS1v15(), hashes.SHA256())

    # --- the two ceremonies ------------------------------------------------------------------------
    def create(self, options, *, origin=None, rp_id=None, flags=None, challenge=None, kind='webauthn.create',
               auth_data=None, fmt='none'):
        self.user_handle = options['user']['id']
        client = self.client_data(kind, challenge or options['challenge'], origin)
        data = auth_data if auth_data is not None else self.auth_data(
            self.flags() if flags is None else flags, attested=True, rp_id=rp_id)
        attestation = cbor2.dumps({'fmt': fmt, 'attStmt': {}, 'authData': data})
        return {'id': b64url_encode(self.credential_id), 'rawId': b64url_encode(self.credential_id),
                'type': 'public-key',
                'response': {'clientDataJSON': b64url_encode(client), 'attestationObject': b64url_encode(attestation),
                             'transports': ['internal', 'hybrid']}}

    def get(self, options, *, origin=None, rp_id=None, flags=None, challenge=None, count=None,
            user_handle='keep', credential_id=None):
        if self.counts and count is None:
            self.sign_count += 1
        client = self.client_data('webauthn.get', challenge or options['challenge'], origin)
        data = self.auth_data(self.flags() if flags is None else flags, rp_id=rp_id, count=count)
        signature = self.sign(data + hashlib.sha256(client).digest())
        handle = self.user_handle if user_handle == 'keep' else user_handle
        cid = b64url_encode(credential_id or self.credential_id)
        return {'id': cid, 'rawId': cid, 'type': 'public-key',
                'response': {'clientDataJSON': b64url_encode(client), 'authenticatorData': b64url_encode(data),
                             'signature': b64url_encode(signature), 'userHandle': handle}}


def new_challenge_b64():
    return b64url_encode(os.urandom(32))


def reg_options(challenge):
    return {'challenge': challenge, 'user': {'id': b64url_encode(os.urandom(32))}}


# ---------------------------------------------------------------------------
# The verification functions on their own
# ---------------------------------------------------------------------------

class WebAuthnVerificationTests(SimpleTestCase):
    def register(self, auth, **kwargs):
        challenge = os.urandom(32)
        cred = auth.create(reg_options(b64url_encode(challenge)), **kwargs)
        return webauthn.verify_registration(cred, challenge=challenge, rp=RP, origins=[ORIGIN])

    def authenticate(self, auth, stored, stored_count=0, **kwargs):
        challenge = os.urandom(32)
        cred = auth.get({'challenge': b64url_encode(challenge)}, **kwargs)
        return webauthn.verify_authentication(cred, challenge=challenge, rp=RP, origins=[ORIGIN],
                                              public_key=stored.public_key, stored_sign_count=stored_count)

    def test_every_algorithm_registers_and_signs_in(self):
        for alg in ('es256', 'eddsa', 'rs256'):
            with self.subTest(alg=alg):
                auth = SoftAuthenticator(alg)
                new = self.register(auth)
                self.assertEqual(new.credential_id, b64url_encode(auth.credential_id))
                self.assertEqual(new.transports, ['internal', 'hybrid'])
                self.assertTrue(new.backup_eligible and new.backed_up)
                result = self.authenticate(auth, new)
                self.assertTrue(result.user_verified)

    def test_attestation_formats_are_not_checked(self):
        # "packed" etc. with an empty statement: accepted, we asked for attestation "none" (see webauthn.py).
        self.register(SoftAuthenticator(), fmt='packed')

    def test_registration_rejects_tampering(self):
        cases = {
            'wrong origin': {'origin': 'https://evil.example'},
            'look-alike origin': {'origin': 'https://attnd.app.evil.example'},
            'wrong challenge': {'challenge': new_challenge_b64()},
            'wrong rp id hash': {'rp_id': 'evil.example'},
            'no user verification': {'flags': FLAG_UP},
            'no user presence': {'flags': FLAG_UV},
            'wrong type': {'kind': 'webauthn.get'},
        }
        for name, kwargs in cases.items():
            with self.subTest(name), self.assertRaises(WebAuthnError):
                self.register(SoftAuthenticator(), **kwargs)

    def test_malformed_authenticator_data(self):
        auth = SoftAuthenticator()
        with self.assertRaises(WebAuthnError):  # bytes after the credential, without the extensions flag
            self.register(auth, auth_data=auth.auth_data(auth.flags(), attested=True, extra=b'\x00'))
        with self.assertRaises(WebAuthnError):  # backed up but not backup eligible
            self.register(auth, auth_data=auth.auth_data(FLAG_UP | FLAG_UV | FLAG_BS, attested=True))
        with self.assertRaises(WebAuthnError):  # no attested credential data
            self.register(auth, auth_data=auth.auth_data(auth.flags()))
        # Extensions after the key are fine when flagged.
        self.register(auth, auth_data=auth.auth_data(auth.flags() | FLAG_ED, attested=True,
                                                     extra=cbor2.dumps({'credProtect': 2})))

    def test_unsupported_keys(self):
        p384 = cbor2.dumps({1: 2, 3: -7, -1: 2, -2: b'\1' * 48, -3: b'\1' * 48})
        off_curve = cbor2.dumps({1: 2, 3: -7, -1: 1, -2: b'\1' * 32, -3: b'\2' * 32})
        small_rsa = rsa.generate_private_key(public_exponent=65537, key_size=1024).public_key().public_numbers()
        rsa1024 = cbor2.dumps({1: 3, 3: -257, -1: small_rsa.n.to_bytes(128, 'big'), -2: b'\1\0\1'})
        for name, key in (('P-384', p384), ('off curve', off_curve), ('RSA 1024', rsa1024), ('not a map', cbor2.dumps([1]))):
            with self.subTest(name), self.assertRaises(WebAuthnError):
                webauthn.load_public_key(key)

    def test_authentication_rejects_tampering(self):
        auth = SoftAuthenticator()
        stored = self.register(auth)
        cases = {
            'wrong origin': {'origin': 'https://evil.example'},
            'wrong challenge': {'challenge': new_challenge_b64()},
            'credential of another rp': {'rp_id': 'dev.attnd.app'},
            'no user verification': {'flags': FLAG_UP},
        }
        for name, kwargs in cases.items():
            with self.subTest(name), self.assertRaises(WebAuthnError):
                self.authenticate(auth, stored, **kwargs)

    def test_signature_from_another_key_is_rejected(self):
        auth = SoftAuthenticator()
        stored = self.register(auth)
        auth.key = ec.generate_private_key(ec.SECP256R1())
        with self.assertRaises(WebAuthnError):
            self.authenticate(auth, stored)

    def test_sign_count(self):
        auth = SoftAuthenticator()
        stored = self.register(auth)
        self.assertEqual(self.authenticate(auth, stored, stored_count=0, count=0).sign_count, 0)  # synced: 0s
        self.assertEqual(self.authenticate(auth, stored, stored_count=5, count=6).sign_count, 6)
        with self.assertLogs('console.webauthn', 'WARNING'), self.assertRaises(WebAuthnError):
            self.authenticate(auth, stored, stored_count=6, count=6)
        with self.assertLogs('console.webauthn', 'WARNING'), self.assertRaises(WebAuthnError):
            self.authenticate(auth, stored, stored_count=9, count=3)

    def test_default_names(self):
        self.assertEqual(webauthn.default_name('Mozilla/5.0 (iPhone; CPU iPhone OS 26_1 like Mac OS X)'), 'iPhone')
        self.assertEqual(webauthn.default_name('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)'), 'Mac')
        self.assertEqual(webauthn.default_name('Mozilla/5.0 (Linux; Android 16; Pixel 10)'), 'Android phone')
        self.assertEqual(webauthn.default_name(''), 'Passkey')

    @override_settings(DEBUG=True, WEBAUTHN_RP_ID='attnd.app')
    def test_localhost_rp_id_in_debug(self):
        from django.test import RequestFactory
        self.assertEqual(webauthn.rp_id(RequestFactory().get('/', HTTP_HOST='localhost:8000')), 'localhost')
        self.assertEqual(webauthn.rp_id(RequestFactory().get('/', HTTP_HOST='attnd.app')), 'attnd.app')


# ---------------------------------------------------------------------------
# Through the views
# ---------------------------------------------------------------------------

@override_settings(WEBAUTHN_RP_ID=RP, WEBAUTHN_ORIGINS=[ORIGIN])
class PasskeyFlowTests(TestCase):
    def setUp(self):
        cache.clear()
        patcher = mock.patch('console.totp.time.time', return_value=NOW)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.staff = User.objects.create_user('s1', 'staff@example.com', 'pw', is_staff=True, is_superuser=True)
        self.organizer = User.objects.create_user('o1', 'org@example.com', 'pw')
        self.admin_url = reverse('admin:index')

    # --- helpers ---------------------------------------------------------------------------------
    def post_json(self, name, body=None, client=None):
        return (client or self.client).post(reverse(name), json.dumps(body or {}), content_type='application/json')

    def sign_in(self, email, client=None):
        return (client or self.client).post(reverse('login'), {'username': email, 'password': 'pw'})

    def add_passkey(self, auth=None, next_url='/console/account/', name='My phone', client=None, **create_kwargs):
        auth = auth or SoftAuthenticator()
        options = self.post_json('passkey_register_options', client=client)
        self.assertEqual(options.status_code, 200, options.content)
        credential = auth.create(options.json(), **create_kwargs)
        response = self.post_json('passkey_register', {'credential': credential, 'name': name, 'next': next_url},
                                  client=client)
        return auth, response

    def enroll_passkey(self, user):
        """A passkey for `user`, set up in a separate session."""
        client = Client()
        client.force_login(user)
        if twofactor.is_required(user) or twofactor.has_second_factor(user):
            session = client.session
            session[twofactor.SESSION_KEY] = str(user.pk)
            session.save()
        auth, response = self.add_passkey(client=client)
        self.assertEqual(response.status_code, 200, response.content)
        return auth

    def passkey_sign_in(self, auth, mode='signin', next_url='/console/', client=None, **get_kwargs):
        options = self.post_json('passkey_auth_options', {'mode': mode}, client=client)
        if options.status_code != 200:
            return options
        credential = auth.get(options.json(), **get_kwargs)
        return self.post_json('passkey_auth', {'mode': mode, 'next': next_url, 'credential': credential}, client=client)

    def totp_code(self, device, offset=0):
        return totp.totp(totp._key(device.secret), NOW + offset)

    def enroll_totp(self, user):
        device = twofactor.start_setup(user)
        twofactor.confirm_setup(device, self.totp_code(device, -30))
        return device

    # --- registration ----------------------------------------------------------------------------
    def test_organizer_adds_a_passkey_from_the_account_page(self):
        self.sign_in('org@example.com')
        account = self.client.get(reverse('console:account'))
        self.assertContains(account, 'data-passkey-register')
        auth, response = self.add_passkey(name='  Work   laptop ')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['redirect'].startswith(reverse('two_factor_recovery_codes')))
        passkey = Passkey.objects.get(user=self.organizer)
        self.assertEqual(passkey.name, 'Work laptop')
        self.assertEqual(passkey.credential_id, b64url_encode(auth.credential_id))
        self.assertTrue(passkey.backed_up)
        self.assertEqual(passkey.transports, ['internal', 'hybrid'])
        # The user handle given to the authenticator is the account's random handle.
        self.assertEqual(auth.user_handle, self.organizer.sign_in_security.user_handle)
        # Recovery codes are shown once, then gone from the session.
        codes_page = self.client.get(response.json()['redirect'])
        self.assertEqual(len(codes_page.context['recovery_codes']), 10)
        self.assertRedirects(self.client.get(reverse('two_factor_recovery_codes') + '?next=/console/account/'),
                             '/console/account/', fetch_redirect_response=False)
        # This session stays signed in; the Account page lists the passkey.
        page = self.client.get(reverse('console:account'))
        self.assertContains(page, 'Work laptop')
        self.assertContains(page, 'Synced')

    def test_options_exclude_existing_passkeys_and_ask_for_user_verification(self):
        auth = self.enroll_passkey(self.organizer)
        self.passkey_sign_in(auth)
        options = self.post_json('passkey_register_options').json()
        self.assertEqual(options['rp'], {'id': RP, 'name': 'Attnd'})
        self.assertEqual(options['authenticatorSelection']['residentKey'], 'required')
        self.assertEqual(options['authenticatorSelection']['userVerification'], 'required')
        self.assertEqual(options['attestation'], 'none')
        self.assertEqual({p['alg'] for p in options['pubKeyCredParams']}, {-7, -8, -257})
        self.assertEqual([c['id'] for c in options['excludeCredentials']], [b64url_encode(auth.credential_id)])

    def test_registration_rejects_bad_responses(self):
        self.client.force_login(self.organizer)
        for name, kwargs in (('origin', {'origin': 'https://evil.example'}), ('challenge', {'challenge': new_challenge_b64()}),
                             ('rp id', {'rp_id': 'evil.example'}), ('uv', {'flags': FLAG_UP})):
            with self.subTest(name), self.assertLogs('console.views_passkeys', 'INFO'):
                _, response = self.add_passkey(**kwargs)
                self.assertEqual(response.status_code, 400)
        self.assertFalse(Passkey.objects.exists())

    def test_registration_challenge_is_single_use(self):
        self.client.force_login(self.organizer)
        auth = SoftAuthenticator()
        credential = auth.create(self.post_json('passkey_register_options').json())
        self.assertEqual(self.post_json('passkey_register', {'credential': credential}).status_code, 200)
        with self.assertLogs('console.views_passkeys', 'INFO'):
            self.assertEqual(self.post_json('passkey_register', {'credential': credential}).status_code, 400)
        self.assertEqual(Passkey.objects.count(), 1)

    def test_password_alone_cannot_add_a_passkey(self):
        """With a second factor pending, adding a passkey would skip it."""
        self.enroll_totp(self.organizer)
        self.sign_in('org@example.com')
        self.assertEqual(self.post_json('passkey_register_options').status_code, 403)
        self.assertEqual(self.post_json('passkey_register', {'credential': {}}).status_code, 403)
        self.client.logout()
        self.assertEqual(self.post_json('passkey_register_options').status_code, 403)

    # --- passkey sign-in -----------------------------------------------------------------------
    def test_passkey_sign_in_counts_as_both_steps(self):
        self.enroll_totp(self.staff)
        auth = self.enroll_passkey(self.staff)
        response = self.passkey_sign_in(auth, next_url=self.admin_url)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json(), {'redirect': self.admin_url})
        self.assertEqual(self.client.session['_auth_user_id'], str(self.staff.pk))
        self.assertEqual(self.client.session[twofactor.SESSION_KEY], str(self.staff.pk))
        self.assertEqual(self.client.get(self.admin_url).status_code, 200)  # no authenticator code asked
        passkey = Passkey.objects.get(user=self.staff)
        self.assertIsNotNone(passkey.last_used_at)

    def test_unsafe_next_is_replaced(self):
        auth = self.enroll_passkey(self.organizer)
        response = self.passkey_sign_in(auth, next_url='https://evil.example/')
        self.assertEqual(response.json()['redirect'], settings.LOGIN_REDIRECT_URL)

    def test_sign_in_rejects_bad_assertions(self):
        auth = self.enroll_passkey(self.staff)
        cases = {
            'wrong origin': {'origin': 'https://evil.example'},
            'credential of another rp': {'rp_id': 'dev.attnd.app'},
            'no user verification': {'flags': FLAG_UP},
            'unknown credential': {'credential_id': os.urandom(32)},
            'no user handle': {'user_handle': None},
            'user handle of another account': {'user_handle': b64url_encode(os.urandom(32))},
        }
        for name, kwargs in cases.items():
            with self.subTest(name), self.assertLogs('console.views_passkeys', 'INFO'):
                response = self.passkey_sign_in(auth, **kwargs)
                self.assertEqual(response.status_code, 400)
                self.assertNotIn('_auth_user_id', self.client.session)

    def test_assertion_cannot_be_replayed(self):
        auth = self.enroll_passkey(self.organizer)
        options = self.post_json('passkey_auth_options').json()
        credential = auth.get(options)
        self.assertEqual(self.post_json('passkey_auth', {'credential': credential}).status_code, 200)
        other = Client()
        self.post_json('passkey_auth_options', client=other)  # a fresh challenge in that session
        with self.assertLogs('console.views_passkeys', 'INFO'):
            self.assertEqual(self.post_json('passkey_auth', {'credential': credential}, client=other).status_code, 400)
        self.assertNotIn('_auth_user_id', other.session)

    def test_autofill_and_button_challenges_both_work(self):
        """The sign-in page's autofill request and the button (or a second tab) each have a challenge."""
        auth = self.enroll_passkey(self.organizer)
        autofill = self.post_json('passkey_auth_options').json()
        button = self.post_json('passkey_auth_options').json()
        self.assertNotEqual(autofill['challenge'], button['challenge'])
        self.assertEqual(self.post_json('passkey_auth', {'credential': auth.get(button)}).status_code, 200)
        # The autofill answer is still valid (once), even though a newer challenge was used meanwhile.
        self.assertEqual(self.post_json('passkey_auth', {'credential': auth.get(autofill)}).status_code, 200)
        with self.assertLogs('console.views_passkeys', 'INFO'):
            self.assertEqual(self.post_json('passkey_auth', {'credential': auth.get(autofill)}).status_code, 400)

    def test_sign_count_regression_is_rejected(self):
        auth = self.enroll_passkey(self.organizer)
        auth.counts = True
        self.assertEqual(self.passkey_sign_in(auth).status_code, 200)
        self.assertEqual(self.passkey_sign_in(auth, client=Client()).status_code, 200)
        self.assertEqual(Passkey.objects.get().sign_count, 2)
        clone = Client()
        with self.assertLogs('console', 'WARNING'):
            response = self.passkey_sign_in(auth, client=clone, count=1)
        self.assertEqual(response.status_code, 400)
        self.assertNotIn('_auth_user_id', clone.session)

    def test_inactive_account_cannot_sign_in(self):
        auth = self.enroll_passkey(self.organizer)
        User.objects.filter(pk=self.organizer.pk).update(is_active=False)
        with self.assertLogs('console.views_passkeys', 'INFO'):
            self.assertEqual(self.passkey_sign_in(auth).status_code, 400)

    @limits(passkey_ip=(2, 900))
    def test_failed_passkey_attempts_are_rate_limited_per_ip(self):
        auth = self.enroll_passkey(self.organizer)
        with self.assertLogs('console.views_passkeys', 'INFO'):
            for _ in range(2):
                self.assertEqual(self.passkey_sign_in(auth, origin='https://evil.example').status_code, 400)
        self.assertEqual(self.passkey_sign_in(auth).status_code, 429)
        self.assertEqual(self.post_json('passkey_auth', {'credential': {}}).status_code, 429)
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_sign_in_page_offers_passkeys(self):
        page = self.client.get(reverse('login'))
        self.assertContains(page, 'autocomplete="username webauthn"')
        self.assertContains(page, 'data-passkey-signin')
        self.assertContains(page, 'console/passkeys.js')
        options = self.post_json('passkey_auth_options').json()
        self.assertEqual(options['allowCredentials'], [])  # usernameless: any passkey for this site
        self.assertEqual(options['userVerification'], 'required')
        self.assertEqual(options['rpId'], RP)

    # --- second step with a passkey ------------------------------------------------------------
    def test_passkey_as_second_step_after_the_password(self):
        auth = self.enroll_passkey(self.staff)
        self.sign_in('staff@example.com')
        response = self.client.get(self.admin_url)
        self.assertTrue(response['Location'].startswith(reverse('two_factor_verify')))
        page = self.client.get(response['Location'])
        self.assertContains(page, 'Use a passkey')
        self.assertNotContains(page, 'Email me a code')
        options = self.post_json('passkey_auth_options', {'mode': 'second_step'}).json()
        self.assertEqual([c['id'] for c in options['allowCredentials']], [b64url_encode(auth.credential_id)])
        response = self.passkey_sign_in(auth, mode='second_step', next_url=self.admin_url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get(self.admin_url).status_code, 200)

    def test_second_step_needs_your_own_passkey(self):
        self.enroll_passkey(self.staff)
        mallory = self.enroll_passkey(self.organizer)
        self.sign_in('staff@example.com')
        with self.assertLogs('console.views_passkeys', 'INFO'):
            response = self.passkey_sign_in(mallory, mode='second_step')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.client.get(self.admin_url).status_code, 302)
        self.assertEqual(self.client.session['_auth_user_id'], str(self.staff.pk))

    def test_second_step_mode_needs_a_pending_password_sign_in(self):
        self.assertEqual(self.post_json('passkey_auth_options', {'mode': 'second_step'}).status_code, 403)
        self.sign_in('org@example.com')  # no second factor: nothing pending
        self.assertEqual(self.post_json('passkey_auth_options', {'mode': 'second_step'}).status_code, 403)

    # --- staff enrollment -------------------------------------------------------------------------
    def test_staff_enroll_with_a_passkey(self):
        self.sign_in('staff@example.com')
        response = self.client.get(self.admin_url)
        self.assertTrue(response['Location'].startswith(reverse('two_factor_setup')))
        setup = self.client.get(response['Location'])
        self.assertContains(setup, 'Add a passkey')
        self.assertContains(setup, 'authenticator app')  # the alternative
        self.assertLess(setup.content.decode().index('Add a passkey'), setup.content.decode().index('Code from the app'))
        _, response = self.add_passkey(next_url=self.admin_url)
        self.assertEqual(response.status_code, 200, response.content)
        codes = self.client.get(response.json()['redirect'])
        self.assertEqual(len(codes.context['recovery_codes']), 10)
        self.assertEqual(self.client.get(self.admin_url).status_code, 200)
        self.assertEqual(twofactor.strong_methods(self.staff), ['passkey'])
        # Next password sign-in: the passkey (or a recovery code) is the second step, no enrollment.
        self.sign_in('staff@example.com')
        self.assertTrue(self.client.get(self.admin_url)['Location'].startswith(reverse('two_factor_verify')))

    def test_email_codes_do_not_satisfy_staff(self):
        SignInSecurity.objects.create(user=self.staff, user_handle='h', email_codes_enabled=True)
        self.assertFalse(twofactor.email_codes_on(self.staff))
        self.sign_in('staff@example.com')
        self.assertTrue(self.client.get(self.admin_url)['Location'].startswith(reverse('two_factor_setup')))
        self.assertEqual(twofactor.check_code(self.staff, '123456'), None)

    def test_staff_cannot_remove_their_last_strong_method(self):
        auth = self.enroll_passkey(self.staff)
        self.passkey_sign_in(auth)
        passkey = Passkey.objects.get(user=self.staff)
        page = self.client.get(reverse('console:account'))
        self.assertNotContains(page, 'value="passkey_remove"')
        self.client.post(reverse('console:account'), {'action': 'passkey_remove', 'passkey': passkey.pk})
        self.assertTrue(Passkey.objects.filter(pk=passkey.pk).exists())
        # With an authenticator app as well, the passkey can go, and then the app can't.
        device = self.enroll_totp(self.staff)
        self.client.post(reverse('console:account'), {'action': 'passkey_remove', 'passkey': passkey.pk})
        self.assertFalse(Passkey.objects.filter(pk=passkey.pk).exists())
        self.client.post(reverse('console:account'), {'action': '2fa_disable', 'code': self.totp_code(device, 30)})
        self.assertTrue(TwoFactorDevice.objects.filter(pk=device.pk).exists())

    def test_staff_with_a_passkey_can_turn_off_the_authenticator_app(self):
        device = self.enroll_totp(self.staff)
        auth = self.enroll_passkey(self.staff)
        self.passkey_sign_in(auth)
        self.client.post(reverse('console:account'), {'action': '2fa_disable', 'code': self.totp_code(device)})
        self.assertFalse(TwoFactorDevice.objects.filter(pk=device.pk).exists())

    def test_rename_and_remove(self):
        auth = self.enroll_passkey(self.organizer)
        self.passkey_sign_in(auth)
        passkey = Passkey.objects.get()
        self.client.post(reverse('console:account'), {'action': 'passkey_rename', 'passkey': passkey.pk, 'name': 'Mac'})
        passkey.refresh_from_db()
        self.assertEqual(passkey.name, 'Mac')
        other = self.enroll_passkey(self.staff)  # someone else's passkey can't be touched
        theirs = Passkey.objects.get(user=self.staff)
        self.client.post(reverse('console:account'), {'action': 'passkey_remove', 'passkey': theirs.pk})
        self.assertTrue(Passkey.objects.filter(pk=theirs.pk).exists())
        self.assertIsNotNone(other)
        self.client.post(reverse('console:account'), {'action': 'passkey_remove', 'passkey': passkey.pk})
        self.assertFalse(Passkey.objects.filter(user=self.organizer).exists())
        self.assertEqual(twofactor.recovery_codes_left(self.organizer), 0)  # nothing left to recover
        # Back to no second factor: a password sign-in goes straight in.
        self.sign_in('org@example.com')
        self.assertEqual(self.client.get(reverse('console:dashboard')).status_code, 200)

    # --- organizers --------------------------------------------------------------------------------
    def test_organizer_with_a_passkey_needs_a_second_step_after_the_password(self):
        self.enroll_passkey(self.organizer)
        self.sign_in('org@example.com')
        response = self.client.get(reverse('console:dashboard'))
        self.assertTrue(response['Location'].startswith(reverse('two_factor_verify')))

    def test_recovery_code_works_for_a_passkey_only_account(self):
        client = Client()
        client.force_login(self.organizer)
        _, response = self.add_passkey(client=client)
        codes = client.get(response.json()['redirect']).context['recovery_codes']
        self.sign_in('org@example.com')
        page = self.client.get(reverse('two_factor_verify'))
        self.assertContains(page, 'one of your recovery codes')
        self.assertNotContains(page, 'authenticator app')
        self.client.post(reverse('two_factor_verify'), {'code': codes[0]})
        self.assertEqual(self.client.get(reverse('console:dashboard')).status_code, 200)

    def test_passkey_prompt_once_after_a_password_sign_in(self):
        self.sign_in('org@example.com')
        first = self.client.get(reverse('console:dashboard'))
        self.assertContains(first, 'Sign in faster with a passkey')
        self.assertNotContains(self.client.get(reverse('console:dashboard')), 'Sign in faster with a passkey')
        self.sign_in('org@example.com')
        self.client.get(reverse('console:dashboard'))
        self.client.post(reverse('passkey_prompt_dismiss'), {'next': '/console/'})
        self.assertTrue(SignInSecurity.objects.get(user=self.organizer).passkey_prompt_dismissed)
        self.sign_in('org@example.com')
        self.assertNotContains(self.client.get(reverse('console:dashboard')), 'Sign in faster with a passkey')

    def test_no_prompt_after_a_passkey_sign_in_or_with_a_passkey(self):
        auth = self.enroll_passkey(self.organizer)
        self.passkey_sign_in(auth)
        self.assertNotContains(self.client.get(reverse('console:dashboard')), 'Sign in faster with a passkey')

    def test_reset_2fa_removes_passkeys(self):
        from django.core.management import call_command
        import io
        self.enroll_passkey(self.staff)
        call_command('reset_2fa', 'staff@example.com', stdout=io.StringIO())
        self.assertFalse(Passkey.objects.filter(user=self.staff).exists())
        self.assertEqual(twofactor.recovery_codes_left(self.staff), 0)


# ---------------------------------------------------------------------------
# Emailed codes
# ---------------------------------------------------------------------------

@override_settings(EMAIL_ENABLED=True)
class EmailCodeTests(TestCase):
    def setUp(self):
        cache.clear()
        self.organizer = User.objects.create_user('o1', 'org@example.com', 'pw')
        self.staff = User.objects.create_user('s1', 'staff@example.com', 'pw', is_staff=True)

    def turn_on(self):
        self.client.force_login(self.organizer)
        response = self.client.post(reverse('console:account'), {'action': 'email_codes_on'})
        self.assertEqual(response.status_code, 200)  # shows the first recovery codes
        self.assertEqual(len(response.context['recovery_codes']), 10)
        self.assertTrue(twofactor.email_codes_on(self.organizer))

    def sign_in(self):
        self.client.post(reverse('login'), {'username': 'org@example.com', 'password': 'pw'})

    def send(self):
        return self.client.post(reverse('two_factor_verify'), {'action': 'send_email', 'next': '/console/'})

    def last_code(self):
        return mail.outbox[-1].subject.rsplit(' ', 1)[-1]

    def test_send_and_verify(self):
        self.turn_on()
        self.sign_in()
        self.assertTrue(self.client.get(reverse('console:dashboard'))['Location'].startswith(reverse('two_factor_verify')))
        page = self.client.get(reverse('two_factor_verify'))
        self.assertContains(page, 'Email me a code')
        self.assertContains(self.send(), 'We emailed a 6-digit code')
        self.assertEqual(mail.outbox[-1].to, ['org@example.com'])
        code = self.last_code()
        self.assertRegex(code, r'^\d{6}$')
        self.assertIn(code, mail.outbox[-1].body)
        row = SignInSecurity.objects.get(user=self.organizer)
        self.assertNotIn(code, row.email_code_hash)  # stored as an HMAC only
        response = self.client.post(reverse('two_factor_verify'), {'code': code, 'next': '/console/'})
        self.assertRedirects(response, '/console/', fetch_redirect_response=False)
        self.assertEqual(self.client.get(reverse('console:dashboard')).status_code, 200)

    def test_single_use(self):
        self.turn_on()
        self.sign_in()
        self.send()
        code = self.last_code()
        self.client.post(reverse('two_factor_verify'), {'code': code})
        self.sign_in()
        response = self.client.post(reverse('two_factor_verify'), {'code': code})
        self.assertContains(response, 'didn’t work')
        self.assertEqual(self.client.get(reverse('console:dashboard')).status_code, 302)

    def test_a_new_code_replaces_the_old_one(self):
        self.turn_on()
        self.sign_in()
        self.send()
        old = self.last_code()
        self.send()
        new = self.last_code()
        if old != new:
            self.assertContains(self.client.post(reverse('two_factor_verify'), {'code': old}), 'didn’t work')
        self.client.post(reverse('two_factor_verify'), {'code': new})
        self.assertEqual(self.client.get(reverse('console:dashboard')).status_code, 200)

    def test_expires_after_ten_minutes(self):
        self.turn_on()
        self.sign_in()
        self.send()
        code = self.last_code()
        later = timezone.now() + timedelta(minutes=10, seconds=1)
        with mock.patch('console.twofactor.timezone.now', return_value=later):
            response = self.client.post(reverse('two_factor_verify'), {'code': code})
        self.assertContains(response, 'didn’t work')
        self.assertEqual(self.client.get(reverse('console:dashboard')).status_code, 302)

    @limits(email_code_user=(2, 900))
    def test_sends_are_rate_limited(self):
        self.turn_on()
        self.sign_in()
        self.send()
        self.send()
        response = self.send()
        self.assertContains(response, 'We’ve sent several codes', status_code=429)
        self.assertEqual(len(mail.outbox), 2)

    @limits(otp_user=(2, 900))
    def test_wrong_codes_are_rate_limited(self):
        self.turn_on()
        self.sign_in()
        self.send()
        code = self.last_code()
        wrong = '000000' if code != '000000' else '111111'
        for _ in range(2):
            self.client.post(reverse('two_factor_verify'), {'code': wrong})
        response = self.client.post(reverse('two_factor_verify'), {'code': code})
        self.assertContains(response, 'Too many wrong codes', status_code=429)

    def test_turn_off(self):
        self.turn_on()
        self.client.post(reverse('console:account'), {'action': 'email_codes_off'})
        self.assertFalse(twofactor.email_codes_on(self.organizer))
        self.assertEqual(twofactor.recovery_codes_left(self.organizer), 0)
        self.sign_in()
        self.assertEqual(self.client.get(reverse('console:dashboard')).status_code, 200)

    def test_staff_cannot_turn_it_on(self):
        self.client.force_login(self.staff)
        session = self.client.session
        session[twofactor.SESSION_KEY] = str(self.staff.pk)
        session.save()
        page = self.client.get(reverse('console:account'))
        self.assertContains(page, 'emailed codes aren’t available for them')
        self.client.post(reverse('console:account'), {'action': 'email_codes_on'})
        self.assertFalse(SignInSecurity.objects.filter(user=self.staff, email_codes_enabled=True).exists())

    @override_settings(EMAIL_ENABLED=False)
    def test_not_offered_without_email(self):
        self.client.force_login(self.organizer)
        self.assertContains(self.client.get(reverse('console:account')), 'Email isn’t set up on this server')
        self.client.post(reverse('console:account'), {'action': 'email_codes_on'})
        self.assertFalse(twofactor.email_codes_on(self.organizer))

    def test_new_recovery_codes_with_the_password(self):
        self.turn_on()
        self.client.post(reverse('console:account'), {'action': '2fa_codes', 'password': 'wrong'})
        response = self.client.post(reverse('console:account'), {'action': '2fa_codes', 'password': 'pw'})
        self.assertEqual(len(response.context['recovery_codes']), 10)


class UnaffectedOrganizerTests(TestCase):
    def test_organizer_without_second_factor_signs_in_with_the_password(self):
        User.objects.create_user('o1', 'org@example.com', 'pw')
        self.client.post(reverse('login'), {'username': 'org@example.com', 'password': 'pw'})
        self.assertEqual(self.client.get(reverse('console:dashboard')).status_code, 200)
        self.assertEqual(self.client.get(reverse('console:account')).status_code, 200)
