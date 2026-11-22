"""Batch 7 Track B: every email through one branded template + plain text, the Emails log (never the body),
one-click unsubscribe (RFC 8058) for optional kinds only, Mailgun's signed webhook and the Architecture status."""
import hashlib
import hmac
import json
import time
from datetime import timedelta

from django.contrib.auth.models import User
from django.core import mail as outbox
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from console import mail
from console.testing import TwoFactorClient
from platform_admin.models import EmailLog


@override_settings(EMAIL_ALLOWED_RECIPIENTS=[], SITE_URL='https://attnd.app')
class SendTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('u', 'org@example.com', 'pw')

    def test_html_and_text_from_one_template(self):
        mail.send('invite', ['helper@example.com'], 'You’re invited', paragraphs=['Hello <there>'],
                  button={'label': 'Sign up', 'url': 'https://attnd.app/console/signup/'})
        message = outbox.outbox[0]
        html = message.alternatives[0][0]
        self.assertEqual(message.alternatives[0][1], 'text/html')
        self.assertIn('Hello &lt;there&gt;', html)  # escaped in HTML
        self.assertIn('Hello <there>', message.body)  # plain in text
        self.assertIn('https://attnd.app/console/signup/', message.body)
        self.assertIn('prefers-color-scheme: dark', html)
        self.assertNotIn('List-Unsubscribe', message.extra_headers)  # transactional
        self.assertEqual(EmailLog.objects.get().kind, 'invite')

    def test_optional_kinds_have_one_click_unsubscribe_and_respect_the_setting(self):
        mail.send('import_done', ['org@example.com', 'other@example.com'], 'Ready', paragraphs=['Done'])
        self.assertEqual(len(outbox.outbox), 2)  # one per person (own link)
        headers = outbox.outbox[0].extra_headers
        self.assertEqual(headers['List-Unsubscribe-Post'], 'List-Unsubscribe=One-Click')
        self.assertTrue(headers['List-Unsubscribe'].startswith('<https://attnd.app/email/unsubscribe/'))
        self.assertIn('Unsubscribe', outbox.outbox[0].alternatives[0][0])
        mail.set_wanted(self.user, 'import_done', False)
        outbox.outbox.clear()
        mail.send('import_done', ['ORG@example.com'], 'Ready', paragraphs=['Done'])
        self.assertEqual(outbox.outbox, [])
        self.assertEqual(EmailLog.objects.filter(status='skipped').count(), 1)

    def test_security_emails_cannot_be_unsubscribed_and_codes_are_masked_in_the_log(self):
        mail.send('sign_in_code', ['org@example.com'], 'Your Attnd sign-in code: 123456', code='123456')
        self.assertNotIn('List-Unsubscribe', outbox.outbox[0].extra_headers)
        row = EmailLog.objects.get()
        self.assertEqual(row.subject, 'Your Attnd sign-in code: ••••••')
        self.assertFalse(hasattr(row, 'body'))
        self.assertIsNone(mail.read_unsubscribe_token(mail.unsubscribe_token(self.user, 'sign_in_code'))[0])

    @override_settings(EMAIL_ALLOWED_RECIPIENTS=['@example.com'])
    def test_allow_list_still_applies(self):
        mail.send('invite', ['someone@elsewhere.org', 'ok@example.com'], 'Hi', paragraphs=['x'])
        self.assertEqual(outbox.outbox[0].to, ['ok@example.com'])
        self.assertEqual(EmailLog.objects.get(to='someone@elsewhere.org').status, 'blocked')

    def test_failures_are_logged(self):
        from unittest import mock
        with mock.patch('django.core.mail.EmailMultiAlternatives.send', side_effect=OSError('down')):
            self.assertEqual(mail.send('invite', ['a@example.com'], 'Hi', paragraphs=['x']), 0)
        row = EmailLog.objects.get()
        self.assertEqual((row.status, row.error), ('failed', 'OSError'))

    def test_every_sender_goes_through_console_mail(self):
        from pathlib import Path
        root = Path(__file__).resolve().parent.parent
        offenders = []
        for path in root.glob('*/**/*.py'):
            if '.venv' in path.parts or path.name.startswith('tests') or 'migrations' in path.parts:
                continue
            text = path.read_text()
            if any(s in text for s in ('import send_mail', 'import EmailMessage', 'mail_admins(')):
                offenders.append(str(path.relative_to(root)))
        self.assertEqual(offenders, [])

    def test_purge(self):
        EmailLog.objects.create(kind='invite', to='a@example.com', subject='x', status='sent',
                                created_at=timezone.now() - timedelta(days=100))
        EmailLog.objects.create(kind='invite', to='b@example.com', subject='x', status='sent')
        self.assertEqual(EmailLog.purge(), 1)


class UnsubscribeTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('u', 'org@example.com', 'pw')
        self.url = reverse('email_unsubscribe', args=[mail.unsubscribe_token(self.user, 'review_waiting')])

    def test_get_asks_and_one_click_post_unsubscribes_without_a_session(self):
        response = self.client.get(self.url, secure=True)
        self.assertContains(response, 'Stop these emails?')
        self.assertTrue(mail.wants(self.user, 'review_waiting'))
        response = self.client.post(self.url, {'List-Unsubscribe': 'One-Click'}, secure=True)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(mail.wants(self.user, 'review_waiting'))
        response = self.client.post(self.url, {'action': 'resubscribe'}, secure=True)
        self.assertContains(response, 'you’ll get these emails again')
        self.assertTrue(mail.wants(self.user, 'review_waiting'))

    def test_bad_tokens(self):
        self.assertEqual(self.client.get('/email/unsubscribe/nope/', secure=True).status_code, 404)


@override_settings(MAILGUN_WEBHOOK_SIGNING_KEY='key-123')
class MailgunWebhookTests(TestCase):
    def payload(self, event, message_id, key='key-123', **extra):
        ts, token = str(int(time.time())), 'tok'
        sig = hmac.new(key.encode(), (ts + token).encode(), hashlib.sha256).hexdigest()
        return json.dumps({'signature': {'timestamp': ts, 'token': token, 'signature': sig},
                           'event-data': {'event': event, 'recipient': 'a@example.com',
                                          'message': {'headers': {'message-id': message_id}}, **extra}})

    def test_events_update_the_log(self):
        row = EmailLog.objects.create(kind='invite', to='a@example.com', subject='x', status='sent', message_id='m1@mg')
        url = reverse('mailgun_webhook')
        self.client.post(url, self.payload('delivered', 'm1@mg'), content_type='application/json', secure=True)
        row.refresh_from_db()
        self.assertEqual(row.status, 'delivered')
        self.client.post(url, self.payload('failed', 'm1@mg', severity='permanent', reason='bounce'),
                         content_type='application/json', secure=True)
        row.refresh_from_db()
        self.assertEqual((row.status, len(row.events)), ('bounced', 2))
        bad = self.client.post(url, self.payload('delivered', 'm1@mg', key='wrong'), content_type='application/json',
                               secure=True)
        self.assertEqual(bad.status_code, 406)

    @override_settings(MAILGUN_WEBHOOK_SIGNING_KEY='')
    def test_off_without_a_key(self):
        self.assertEqual(self.client.post(reverse('mailgun_webhook'), '{}', content_type='application/json',
                                          secure=True).status_code, 404)


class EmailsPageTests(TestCase):
    def test_staff_only_and_in_the_platform_nav(self):
        EmailLog.objects.create(kind='new_sign_in', to='a@example.com', subject='New sign-in to Attnd', status='failed')
        organizer = User.objects.create_user('o', 'o@example.com', 'pw')
        client = TwoFactorClient()
        client.force_login(organizer)
        self.assertEqual(client.get(reverse('platform:emails'), secure=True).status_code, 404)
        staff = User.objects.create_user('s', 's@example.com', 'pw', is_staff=True)
        client.force_login(staff)
        response = client.get(reverse('platform:emails') + '?status=failed', secure=True)
        self.assertContains(response, 'New sign-in to Attnd')
        self.assertContains(response, 'aria-current="page">Emails<')

    def test_architecture_status(self):
        from platform_admin import architecture
        EmailLog.objects.create(kind='invite', to='a@example.com', subject='x', status='sent')
        EmailLog.objects.create(kind='invite', to='b@example.com', subject='x', status='bounced')
        self.assertEqual(architecture.status()['mailgun']['state'], 'amber')
