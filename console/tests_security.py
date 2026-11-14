"""Rate limits, two-factor sign-in, Content Security Policy and the admin's location."""

import hashlib
import io
import re
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.contrib.auth.models import User
from django.core import mail
from django.core.cache import cache
from django.core.management import call_command
from django.test import Client, RequestFactory, TestCase, override_settings
from django.urls import reverse

from attnd.ratelimit import RateLimit, client_ip
from console import totp, twofactor
from console.models import TwoFactorDevice
from events.models import Event

NOW = 1_800_000_000.0  # fixed clock for TOTP tests


def limits(**overrides):
    return override_settings(RATE_LIMITS={**settings.RATE_LIMITS, **overrides})


class ClientIpTests(TestCase):
    def test_uses_the_last_forwarded_hop(self):
        request = RequestFactory().get('/', HTTP_X_FORWARDED_FOR='6.6.6.6, 1.2.3.4', REMOTE_ADDR='10.0.0.1')
        self.assertEqual(client_ip(request), '1.2.3.4')

    def test_single_hop_and_whitespace(self):
        request = RequestFactory().get('/', HTTP_X_FORWARDED_FOR=' 1.2.3.4 ')
        self.assertEqual(client_ip(request), '1.2.3.4')

    def test_falls_back_to_remote_addr(self):
        self.assertEqual(client_ip(RequestFactory().get('/', REMOTE_ADDR='10.0.0.1')), '10.0.0.1')

    @override_settings(RATE_LIMIT_TRUST_X_FORWARDED_FOR=False)
    def test_header_ignored_when_not_trusted(self):
        request = RequestFactory().get('/', HTTP_X_FORWARDED_FOR='1.2.3.4', REMOTE_ADDR='10.0.0.1')
        self.assertEqual(client_ip(request), '10.0.0.1')

    def test_counter_keys_hold_no_personal_data(self):
        key = RateLimit('login_email').key('Someone@Example.com')
        self.assertNotIn('example', key.lower())
        self.assertEqual(key, RateLimit('login_email').key('someone@example.com'))


class LoginRateLimitTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user('u1', 'person@example.com', 'right-password')
        self.url = reverse('login')

    def attempt(self, email='person@example.com', password='wrong', ip='1.1.1.1'):
        return self.client.post(self.url, {'username': email, 'password': password}, HTTP_X_FORWARDED_FOR=ip)

    def test_locks_an_email_after_five_failures(self):
        for _ in range(5):
            self.assertEqual(self.attempt().status_code, 200)
        response = self.attempt(password='right-password')  # even the right password waits
        self.assertEqual(response.status_code, 429)
        self.assertContains(response, 'Too many sign-in attempts', status_code=429)
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_unknown_email_gets_the_same_answer(self):
        for _ in range(5):
            self.attempt(email='nobody@example.com')
        known = [self.attempt(ip='2.2.2.2') for _ in range(6)][-1]
        unknown = self.attempt(email='nobody@example.com', ip='3.3.3.3')
        self.assertEqual(unknown.status_code, known.status_code)
        self.assertIn('Too many sign-in attempts', unknown.content.decode())
        self.assertIn('Too many sign-in attempts', known.content.decode())

    def test_success_clears_the_email_count(self):
        for _ in range(4):
            self.attempt()
        self.assertEqual(self.attempt(password='right-password').status_code, 302)
        self.client.logout()
        for _ in range(4):
            self.assertEqual(self.attempt().status_code, 200)
        self.assertEqual(self.attempt(password='right-password').status_code, 302)

    @limits(login_ip=(3, 900))
    def test_locks_an_ip_across_emails(self):
        for n in range(3):
            self.attempt(email=f'guess{n}@example.com', ip='9.9.9.9')
        self.assertEqual(self.attempt(password='right-password', ip='9.9.9.9').status_code, 429)
        self.assertEqual(self.attempt(password='right-password', ip='8.8.8.8').status_code, 302)

    def test_admin_login_page_goes_to_the_rate_limited_sign_in(self):
        response = self.client.get('/' + settings.ADMIN_URL_PATH + 'login/?next=/x/')
        self.assertRedirects(response, self.url + '?next=%2Fx%2F', fetch_redirect_response=False)


class PasswordResetRateLimitTests(TestCase):
    def setUp(self):
        cache.clear()
        User.objects.create_user('u1', 'person@example.com', 'pw')
        self.url = reverse('password_reset')

    def test_three_requests_per_email_per_hour(self):
        for n in range(3):
            response = self.client.post(self.url, {'email': 'person@example.com'}, HTTP_X_FORWARDED_FOR=f'1.1.1.{n}')
            self.assertEqual(response.status_code, 302)
        response = self.client.post(self.url, {'email': 'person@example.com'}, HTTP_X_FORWARDED_FOR='1.1.1.9')
        self.assertContains(response, 'Too many reset requests', status_code=429)
        self.assertEqual(len(mail.outbox), 3)

    def test_unknown_email_is_limited_the_same_way(self):
        for _ in range(3):
            self.client.post(self.url, {'email': 'nobody@example.com'})
        response = self.client.post(self.url, {'email': 'nobody@example.com'})
        self.assertContains(response, 'Too many reset requests', status_code=429)
        self.assertEqual(len(mail.outbox), 0)

    @limits(reset_ip=(2, 3600))
    def test_per_ip_limit(self):
        for n in range(2):
            self.client.post(self.url, {'email': f'x{n}@example.com'}, HTTP_X_FORWARDED_FOR='5.5.5.5')
        response = self.client.post(self.url, {'email': 'person@example.com'}, HTTP_X_FORWARDED_FOR='5.5.5.5')
        self.assertEqual(response.status_code, 429)
        self.assertEqual(len(mail.outbox), 0)


class SignupRateLimitTests(TestCase):
    def setUp(self):
        cache.clear()

    @limits(signup_ip=(2, 3600))
    def test_signups_per_ip(self):
        url = reverse('console:signup')
        for n in range(2):
            response = self.client.post(url, {'email': f'new{n}@example.com', 'password': 'a-Long-pass-123',
                                               'password2': 'a-Long-pass-123'}, HTTP_X_FORWARDED_FOR='4.4.4.4')
            self.assertNotEqual(response.status_code, 429)
            self.client.logout()
        response = self.client.post(url, {'email': 'new9@example.com', 'password': 'a-Long-pass-123',
                                           'password2': 'a-Long-pass-123'}, HTTP_X_FORWARDED_FOR='4.4.4.4')
        self.assertContains(response, 'Too many sign-up attempts', status_code=429)
        self.assertFalse(User.objects.filter(email='new9@example.com').exists())


class TotpTests(TestCase):
    """RFC 6238 appendix B (8 digits; the seeds are ASCII '1234567890' repeated)."""
    VECTORS = [
        (59, '94287082', '46119246', '90693936'),
        (1111111109, '07081804', '68084774', '25091201'),
        (1111111111, '14050471', '67062674', '99943326'),
        (1234567890, '89005924', '91819424', '93441116'),
        (2000000000, '69279037', '90698825', '38618901'),
        (20000000000, '65353130', '77737706', '47863826'),
    ]

    def test_rfc6238_vectors(self):
        seeds = {hashlib.sha1: b'12345678901234567890', hashlib.sha256: b'12345678901234567890123456789012',
                 hashlib.sha512: b'1234567890' * 6 + b'1234'}
        for at, sha1, sha256, sha512 in self.VECTORS:
            for digest, expected in ((hashlib.sha1, sha1), (hashlib.sha256, sha256), (hashlib.sha512, sha512)):
                self.assertEqual(totp.totp(seeds[digest], at=at, digits=8, digest=digest), expected,
                                 (at, digest().name))

    def test_rfc4226_hotp_vectors(self):
        expected = ['755224', '287082', '359152', '969429', '338314', '254676', '287922', '162583', '399871', '520489']
        self.assertEqual([totp.hotp(b'12345678901234567890', n) for n in range(10)], expected)

    def test_window_and_reuse(self):
        secret = totp.new_secret()
        key = totp._key(secret)
        step = totp.time_step(NOW)
        self.assertEqual(totp.match_step(secret, totp.totp(key, NOW), at=NOW), step)
        self.assertEqual(totp.match_step(secret, totp.totp(key, NOW - 30), at=NOW), step - 1)
        self.assertEqual(totp.match_step(secret, totp.totp(key, NOW + 30), at=NOW), step + 1)
        self.assertIsNone(totp.match_step(secret, totp.totp(key, NOW - 90), at=NOW))
        self.assertIsNone(totp.match_step(secret, totp.totp(key, NOW), after_step=step, at=NOW))
        self.assertIsNone(totp.match_step(secret, 'abcdef', at=NOW))

    def test_provisioning_uri(self):
        uri = totp.provisioning_uri('JBSWY3DPEHPK3PXP', 'a@example.com', 'Attnd')
        self.assertTrue(uri.startswith('otpauth://totp/Attnd:a@example.com?'))
        self.assertIn('secret=JBSWY3DPEHPK3PXP', uri)
        self.assertIn('issuer=Attnd', uri)


class TwoFactorTests(TestCase):
    def setUp(self):
        cache.clear()
        patcher = mock.patch('console.totp.time.time', return_value=NOW)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.staff = User.objects.create_user('s1', 'staff@example.com', 'pw', is_staff=True, is_superuser=True)
        self.organizer = User.objects.create_user('o1', 'org@example.com', 'pw')
        self.admin_url = reverse('admin:index')

    def code(self, device, offset=0):
        return totp.totp(totp._key(device.secret), NOW + offset)

    def enroll(self, user):
        device = twofactor.start_setup(user)
        codes = twofactor.confirm_setup(device, self.code(device, -30))  # leave the current step unused
        return device, codes

    def sign_in(self, email, password='pw'):
        return self.client.post(reverse('login'), {'username': email, 'password': password})

    # --- regular organizers -------------------------------------------------

    def test_regular_user_without_2fa_is_unaffected(self):
        self.sign_in('org@example.com')
        self.assertEqual(self.client.get(reverse('console:dashboard')).status_code, 200)

    def test_regular_user_with_2fa_must_enter_a_code(self):
        device, _ = self.enroll(self.organizer)
        self.sign_in('org@example.com')
        response = self.client.get(reverse('console:dashboard'))
        self.assertRedirects(response, reverse('two_factor_verify') + '?next=%2Fconsole%2F', fetch_redirect_response=False)
        response = self.client.post(reverse('two_factor_verify'), {'code': self.code(device), 'next': '/console/'})
        self.assertRedirects(response, '/console/', fetch_redirect_response=False)
        self.assertEqual(self.client.get(reverse('console:dashboard')).status_code, 200)

    def test_optional_enrollment_from_the_account_page(self):
        self.client.force_login(self.organizer)
        self.assertContains(self.client.get(reverse('console:account')), 'Set up two-factor sign-in')
        page = self.client.get(reverse('two_factor_setup'))
        self.assertContains(page, '<svg')
        device = TwoFactorDevice.objects.get(user=self.organizer)
        self.assertContains(page, device.secret[:4])
        self.assertContains(page, 'otpauth://totp/')
        self.assertFalse(device.confirmed)
        self.assertEqual(self.client.post(reverse('two_factor_setup'), {'code': '000000'}).status_code, 200)
        device.refresh_from_db()
        self.assertFalse(device.confirmed)
        response = self.client.post(reverse('two_factor_setup'), {'code': self.code(device), 'next': '/console/account/'})
        device.refresh_from_db()
        self.assertTrue(device.confirmed)
        codes = response.context['recovery_codes']
        self.assertEqual(len(codes), 10)
        for code in codes:
            self.assertContains(response, code)
            self.assertNotIn(code, str(device.recovery_codes))  # stored hashed only
        self.assertEqual(self.client.get(reverse('console:account')).status_code, 200)

    def test_regular_user_can_turn_it_off_with_a_code(self):
        device, _ = self.enroll(self.organizer)
        self.client.force_login(self.organizer)
        self.client.post(reverse('two_factor_verify'), {'code': self.code(device)})
        self.client.post(reverse('console:account'), {'action': '2fa_disable', 'code': '123456'})
        self.assertTrue(TwoFactorDevice.objects.filter(pk=device.pk).exists())
        self.client.post(reverse('console:account'), {'action': '2fa_disable', 'code': self.code(device, 30)})
        self.assertFalse(TwoFactorDevice.objects.filter(pk=device.pk).exists())

    # --- staff ----------------------------------------------------------------

    def test_staff_without_2fa_must_enroll_before_console_or_admin(self):
        self.sign_in('staff@example.com')
        for url in (reverse('console:dashboard'), self.admin_url, reverse('console:admin_usage')):
            response = self.client.get(url)
            self.assertEqual(response.status_code, 302, url)
            self.assertTrue(response['Location'].startswith(reverse('two_factor_setup')), url)
        setup = self.client.get(reverse('two_factor_setup') + '?next=' + self.admin_url)
        self.assertContains(setup, 'Admin accounts need a second step')
        device = TwoFactorDevice.objects.get(user=self.staff)
        self.client.post(reverse('two_factor_setup'), {'code': self.code(device), 'next': self.admin_url})
        self.assertEqual(self.client.get(self.admin_url).status_code, 200)

    def test_admin_blocked_until_verified(self):
        device, _ = self.enroll(self.staff)
        self.sign_in('staff@example.com')
        response = self.client.get(self.admin_url)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response['Location'].startswith(reverse('two_factor_verify')))
        # POSTs are blocked too (no redirect back to a form post).
        self.assertEqual(self.client.post(reverse('admin:auth_user_changelist')).status_code, 302)
        self.assertEqual(self.client.get(reverse('password_change')).status_code, 302)
        wrong = self.client.post(reverse('two_factor_verify'), {'code': '000000', 'next': self.admin_url})
        self.assertContains(wrong, 'didn’t work')
        ok = self.client.post(reverse('two_factor_verify'), {'code': self.code(device), 'next': self.admin_url})
        self.assertRedirects(ok, self.admin_url, fetch_redirect_response=False)
        self.assertEqual(self.client.get(self.admin_url).status_code, 200)

    def test_new_login_needs_the_second_step_again(self):
        device, _ = self.enroll(self.staff)
        self.sign_in('staff@example.com')
        self.client.post(reverse('two_factor_verify'), {'code': self.code(device)})
        self.assertEqual(self.client.get(self.admin_url).status_code, 200)
        self.sign_in('staff@example.com')
        self.assertEqual(self.client.get(self.admin_url).status_code, 302)

    def test_a_code_works_only_once(self):
        device, _ = self.enroll(self.staff)
        code = self.code(device)
        self.sign_in('staff@example.com')
        self.client.post(reverse('two_factor_verify'), {'code': code})
        self.assertEqual(self.client.get(self.admin_url).status_code, 200)
        other = Client()
        other.post(reverse('login'), {'username': 'staff@example.com', 'password': 'pw'})
        response = other.post(reverse('two_factor_verify'), {'code': code})
        self.assertEqual(response.status_code, 200)  # rejected, form shown again
        self.assertEqual(other.get(self.admin_url).status_code, 302)

    def test_recovery_code_is_single_use(self):
        device, codes = self.enroll(self.staff)
        self.sign_in('staff@example.com')
        response = self.client.post(reverse('two_factor_verify'), {'code': codes[0].upper().replace('-', ' ')})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.get(self.admin_url).status_code, 200)
        device.refresh_from_db()
        self.assertEqual(len(device.recovery_codes), 9)
        self.sign_in('staff@example.com')
        self.assertEqual(self.client.post(reverse('two_factor_verify'), {'code': codes[0]}).status_code, 200)
        self.assertEqual(self.client.get(self.admin_url).status_code, 302)

    @limits(otp_user=(2, 900))
    def test_code_attempts_are_rate_limited(self):
        device, _ = self.enroll(self.staff)
        self.sign_in('staff@example.com')
        for _ in range(2):
            self.client.post(reverse('two_factor_verify'), {'code': '000000'})
        response = self.client.post(reverse('two_factor_verify'), {'code': self.code(device)})
        self.assertContains(response, 'Too many wrong codes', status_code=429)
        self.assertEqual(self.client.get(self.admin_url).status_code, 302)

    def test_staff_cannot_turn_it_off(self):
        device, _ = self.enroll(self.staff)
        self.sign_in('staff@example.com')
        self.client.post(reverse('two_factor_verify'), {'code': self.code(device)})
        self.client.post(reverse('console:account'), {'action': '2fa_disable', 'code': self.code(device, 30)})
        self.assertTrue(TwoFactorDevice.objects.filter(pk=device.pk).exists())

    def test_new_recovery_codes_from_the_account_page(self):
        device, old = self.enroll(self.organizer)
        self.client.force_login(self.organizer)
        self.client.post(reverse('two_factor_verify'), {'code': self.code(device)})
        response = self.client.post(reverse('console:account'), {'action': '2fa_codes', 'code': self.code(device, 30)})
        new = response.context['recovery_codes']
        self.assertEqual(len(new), 10)
        self.assertFalse(set(new) & set(old))

    def test_reset_2fa_command(self):
        self.enroll(self.staff)
        out = io.StringIO()
        call_command('reset_2fa', 'STAFF@example.com', stdout=out)
        self.assertFalse(TwoFactorDevice.objects.filter(user=self.staff).exists())
        self.assertIn('staff@example.com', out.getvalue())
        self.sign_in('staff@example.com')
        response = self.client.get(self.admin_url)
        self.assertTrue(response['Location'].startswith(reverse('two_factor_setup')))


class AdminPathTests(TestCase):
    def test_admin_moved(self):
        self.assertEqual(reverse('admin:index'), '/' + settings.ADMIN_URL_PATH)
        self.assertNotEqual(settings.ADMIN_URL_PATH, 'admin/')
        self.assertEqual(self.client.get('/admin/').status_code, 404)
        self.assertEqual(self.client.get('/admin/login/').status_code, 404)


@override_settings(SECURE_SSL_REDIRECT=False)
class ContentSecurityPolicyTests(TestCase):
    def test_header_with_nonce_matching_the_inline_scripts(self):
        owner = User.objects.create_user('o1', 'org@example.com', 'pw')
        event = Event.objects.create(name='Fair', event_code='FAIR1', user=owner, timezone='UTC',
                                     start_time='2030-01-01T00:00:00Z')
        self.client.force_login(owner)
        response = self.client.get(reverse('console:event', args=[event.pk]))
        self.assertEqual(response.status_code, 200)
        policy = response['Content-Security-Policy']
        nonce = re.search(r"'nonce-([^']+)'", policy).group(1)
        self.assertIn(f'nonce="{nonce}"', response.content.decode())
        for directive in ("default-src 'self'", "frame-ancestors 'none'", "base-uri 'self'",
                          "form-action 'self'", "object-src 'none'"):
            self.assertIn(directive, policy)
        self.assertNotIn("'unsafe-inline'", re.search(r'script-src[^;]*', policy).group(0))

    def test_every_response_gets_the_policy(self):
        for url in ('/', reverse('login'), '/join/NOPE/'):
            self.assertIn("default-src 'self'", self.client.get(url)['Content-Security-Policy'], url)

    @override_settings(SECURE_CSP={}, SECURE_CSP_REPORT_ONLY=settings.CSP_POLICY)
    def test_report_only_mode(self):
        response = self.client.get('/')
        self.assertNotIn('Content-Security-Policy', response)
        self.assertIn("default-src 'self'", response['Content-Security-Policy-Report-Only'])

    def test_violation_reports_are_logged(self):
        cache.clear()
        with self.assertLogs('attnd.csp', 'WARNING') as logs:
            response = self.client.post('/csp-report/', '{"csp-report": {"violated-directive": "script-src", '
                                        '"blocked-uri": "inline", "document-uri": "https://attnd.app/"}}',
                                        content_type='application/csp-report')
        self.assertEqual(response.status_code, 204)
        self.assertIn('script-src', logs.output[0])

    def test_templates_have_no_inline_handlers_or_unnonced_scripts(self):
        """Inline event handlers (onclick=…) and <script> blocks without a nonce are blocked by the policy."""
        root = Path(settings.BASE_DIR)
        templates = [p for p in root.rglob('*.html')
                     if '.venv' not in p.parts and 'staticfiles' not in p.parts and 'node_modules' not in p.parts]
        self.assertGreater(len(templates), 20)
        handler = re.compile(r'<[a-zA-Z][^>]*\son[a-z]+\s*=', re.S)
        script = re.compile(r'<script\b([^>]*)>', re.I)
        problems = []
        for path in templates:
            text = path.read_text()
            if handler.search(text):
                problems.append(f'{path.relative_to(root)}: inline event handler')
            for attrs in script.findall(text):
                if 'src=' not in attrs and 'nonce="{{ csp_nonce }}"' not in attrs:
                    problems.append(f'{path.relative_to(root)}: <script{attrs}> without a nonce')
            if re.search(r'href\s*=\s*["\']\s*javascript:', text, re.I):
                problems.append(f'{path.relative_to(root)}: javascript: URL')
        self.assertEqual(problems, [])
