"""The website (ticket page → hold → Checkout → confirmation, lookup, transfer, refunds), Wallet passes, the console
and the demo kit."""

import io
import json
import shutil
import subprocess
import tempfile
import zipfile
from datetime import datetime, timedelta, timezone as dt_timezone
from pathlib import Path
from unittest.mock import patch

import jwt
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from cryptography.x509.oid import NameOID
from django.contrib.auth.models import User
from django.core import mail
from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from scrape.models import Change, ChangeSet, IngestJob
from studio.models import StudioAccess
from tickets import holders, inventory, payments, signing, wallet
from tickets.fakes import STRIPE_SETTINGS, FakeStripe, completed_event, ticketed_event, webhook
from tickets.models import (DoorStaff, Order, RefundRequest, Ticket, TicketSettings, TicketType, WaitlistEntry,
                            WalletPass, WalletRegistration)
from tickets.tests_api import issue


@override_settings(**STRIPE_SETTINGS, SITE_URL='https://attnd.app')
class BuyerWebTests(TestCase):
    def setUp(self):
        cache.clear()
        self.stripe = FakeStripe().install(self)
        self.event, self.ga, self.free = ticketed_event()

    def test_page_shows_all_in_prices(self):
        resp = self.client.get('/t/tixfest/', secure=True)
        self.assertContains(resp, '$26.06')  # $25 + its processing fee, up front
        self.assertContains(resp, 'incl. fees')
        self.assertNotContains(resp, '$25.00</div>')
        self.assertEqual(self.client.get('/t/NOPE/', secure=True).status_code, 404)
        self.event.plan = 'standard'
        self.event.save()
        self.assertEqual(self.client.get('/t/TIXFEST/', secure=True).status_code, 404)

    def test_buy_flow_to_checkout_and_confirmation(self):
        resp = self.client.post('/t/TIXFEST/', {f'qty_{self.ga.id}': '2'}, secure=True)
        order = Order.objects.get()
        self.assertRedirects(resp, payments.order_url(order), fetch_redirect_response=False)
        page = self.client.get(payments.order_url(order), secure=True)
        self.assertContains(page, 'Processing fee')
        self.assertContains(page, 'Pay $51.81')
        resp = self.client.post(payments.order_url(order), {'action': 'pay', 'email': 'b@example.com', 'name': 'Bea',
                                                            'terms': '1'}, secure=True)
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(resp['Location'].startswith('https://checkout.stripe.com/'))
        order.refresh_from_db()
        self.stripe.amount_received = order.total_cents
        with self.captureOnCommitCallbacks(execute=True):
            webhook(self.client, completed_event(order))
        done = self.client.get(payments.order_url(order) + '?paid=1', secure=True)
        self.assertContains(done, 'You’re going!')
        t = order.tickets.first()
        self.assertContains(done, f'/tickets/ticket/{t.public_id}/{t.key}/qr.svg')
        qr = self.client.get(f'/tickets/ticket/{t.public_id}/{t.key}/qr.svg', secure=True)
        self.assertEqual(qr['Content-Type'], 'image/svg+xml')
        self.assertEqual(self.client.get(f'/tickets/ticket/{t.public_id}/wrong/', secure=True).status_code, 404)

    def test_details_validation_and_free_tickets(self):
        self.client.post('/t/TIXFEST/', {f'qty_{self.free.id}': '1'}, secure=True)
        order = Order.objects.get()
        url = payments.order_url(order)
        self.assertEqual(self.client.post(url, {'action': 'pay', 'email': 'bad', 'name': 'x', 'terms': '1'},
                                          secure=True).status_code, 400)
        with self.captureOnCommitCallbacks(execute=True):
            resp = self.client.post(url, {'action': 'pay', 'email': 'f@example.com', 'name': 'Fay', 'terms': '1'},
                                    secure=True)
        self.assertRedirects(resp, url, fetch_redirect_response=False)
        self.assertEqual(order.tickets.count(), 1)
        self.assertEqual(self.stripe.calls, [])
        self.assertEqual(mail.outbox[0].to, ['f@example.com'])

    def test_questions_are_required_and_stored_per_ticket(self):
        ts = inventory.ticketing(self.event)
        ts.questions = [{'id': 'size', 'label': 'Size', 'kind': 'select', 'options': ['S', 'M'], 'required': True,
                         'per_ticket': True}]
        ts.save()
        self.client.post('/t/TIXFEST/', {f'qty_{self.free.id}': '2'}, secure=True)
        url = payments.order_url(Order.objects.get())
        base = {'action': 'pay', 'email': 'q@example.com', 'name': 'Q', 'terms': '1'}
        self.assertContains(self.client.post(url, base, secure=True), 'Ticket 1', status_code=400)
        self.client.post(url, {**base, 't0_q_size': 'S', 't1_q_size': 'M', 't1_name': 'Kid'}, secure=True)
        tickets = list(Order.objects.get().tickets.order_by('id'))
        self.assertEqual([t.answers['size'] for t in tickets], ['S', 'M'])
        self.assertEqual(tickets[1].holder_name, 'Kid')

    def test_cancel_hold_unlock_and_waitlist(self):
        self.client.post('/t/TIXFEST/', {f'qty_{self.ga.id}': '1'}, secure=True)
        order = Order.objects.get()
        self.client.post(payments.order_url(order), {'action': 'cancel'}, secure=True)
        self.assertEqual(Order.objects.get().status, Order.CANCELLED)
        TicketType.objects.create(event=self.event, name='Backstage', price_cents=9000, hidden=True,
                                  unlock_code='VIPNOW')
        self.assertNotContains(self.client.get('/t/TIXFEST/', secure=True), 'Backstage')
        self.client.post('/t/TIXFEST/', {'action': 'unlock', 'unlock': 'vipnow'}, secure=True)
        self.assertContains(self.client.get('/t/TIXFEST/', secure=True), 'Backstage')
        self.free.quantity = 0
        self.free.save()
        self.client.post('/t/TIXFEST/waitlist/', {'type': self.free.id, 'email': 'w@example.com'}, secure=True)
        self.assertTrue(WaitlistEntry.objects.filter(email='w@example.com').exists())
        self.free.quantity = 5
        self.free.save()
        from tickets import waitlist
        self.assertEqual(waitlist.notify_if_available(self.event), 1)
        self.assertIn('available', mail.outbox[-1].subject)

    def test_lookup_by_email_code(self):
        issue(self.event, self.free, email='me@example.com')
        with patch('tickets.buyers.secrets.randbelow', return_value=424242):
            resp = self.client.post('/tickets/lookup/', {'action': 'send', 'email': 'me@example.com'}, secure=True)
        self.assertContains(resp, 'We sent a 6-digit code')
        self.assertContains(self.client.post('/tickets/lookup/', {'action': 'verify', 'email': 'me@example.com',
                                                                  'code': '111111'}, secure=True),
                            'isn’t right', status_code=400)
        resp = self.client.post('/tickets/lookup/', {'action': 'verify', 'email': 'me@example.com', 'code': '424242'},
                                secure=True)
        self.assertRedirects(resp, '/tickets/mine/', fetch_redirect_response=False)
        self.assertContains(self.client.get('/tickets/mine/', secure=True), 'Tix Fest')

    def test_transfer_resend_and_refund_request(self):
        ticket = issue(self.event, self.free, email='me@example.com')[0]
        old_token, old_key = signing.token_for(ticket), ticket.key
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(f'/tickets/ticket/{ticket.public_id}/{ticket.key}/', {
                'action': 'transfer', 'email': 'friend@example.com', 'name': 'Fran'}, secure=True)
        ticket.refresh_from_db()
        self.assertEqual((ticket.holder_email, ticket.version), ('friend@example.com', 2))
        self.assertNotEqual(ticket.key, old_key)  # the old link stops working
        self.assertEqual(self.client.get(f'/tickets/ticket/{ticket.public_id}/{old_key}/', secure=True).status_code,
                         404)
        self.assertIn('friend@example.com', mail.outbox[-1].to)
        from tickets import checkin
        self.assertEqual(checkin.sync(self.event, [{'client_id': 'z', 'token': old_token}])[0]['result'], 'revoked')
        order = ticket.order
        self.client.post(f'/tickets/order/{order.public_id}/{order.key}/resend/', secure=True)
        self.assertIn('Your tickets', mail.outbox[-1].subject)
        self.client.post(f'/tickets/order/{order.public_id}/{order.key}/refund/', {'reason': 'Sick'}, secure=True)
        self.assertTrue(RefundRequest.objects.filter(order=order, status='pending').exists())

    def test_embed_can_be_framed(self):
        resp = self.client.get('/t/TIXFEST/embed/', secure=True)
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn('X-Frame-Options', resp)
        self.assertIn("frame-ancestors *", resp.get('Content-Security-Policy', ''))
        self.assertIn('DENY', self.client.get('/t/TIXFEST/', secure=True).get('X-Frame-Options', ''))

    def test_checkout_redirect_allowed_by_csp(self):
        csp = self.client.get('/t/TIXFEST/', secure=True)['Content-Security-Policy']
        self.assertIn('form-action', csp)
        self.assertIn('https://checkout.stripe.com', csp)


def make_cert(subject, issuer_key, issuer_name, key, ca=False):
    now = datetime.now(dt_timezone.utc)
    return (x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, subject)]))
            .issuer_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, issuer_name)]))
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(days=1)).not_valid_after(now + timedelta(days=30))
            .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True)
            .sign(issuer_key, hashes.SHA256()))


def pem(obj):
    if hasattr(obj, 'public_bytes') and isinstance(obj, x509.Certificate):
        return obj.public_bytes(serialization.Encoding.PEM).decode()
    return obj.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                             serialization.NoEncryption()).decode()


WWDR_KEY = ec.generate_private_key(ec.SECP256R1())
WWDR = make_cert('Test WWDR', WWDR_KEY, 'Test WWDR', WWDR_KEY, ca=True)
PASS_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
PASS_CERT = make_cert('Pass Type ID: pass.com.arcu.attnd.tickets', WWDR_KEY, 'Test WWDR', PASS_KEY)
GOOGLE_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
GOOGLE_SA = json.dumps({'client_email': 'wallet@test.iam.gserviceaccount.com', 'private_key': pem(GOOGLE_KEY),
                        'private_key_id': 'k1'})


@override_settings(APPLE_WALLET_CERT_PEM=pem(PASS_CERT), APPLE_WALLET_KEY_PEM=pem(PASS_KEY),
                   APPLE_WALLET_WWDR_PEM=pem(WWDR), GOOGLE_WALLET_ISSUER_ID='3388000000012345678',
                   GOOGLE_WALLET_SERVICE_ACCOUNT_JSON=GOOGLE_SA, SITE_URL='https://attnd.app', **STRIPE_SETTINGS)
class WalletTests(TestCase):
    def setUp(self):
        self.event, self.ga, self.free = ticketed_event()
        self.ticket = issue(self.event, self.free, name='Ada Holder')[0]

    def pkpass(self):
        resp = self.client.get(f'/tickets/ticket/{self.ticket.public_id}/{self.ticket.key}/wallet.pkpass', secure=True)
        self.assertEqual(resp['Content-Type'], 'application/vnd.apple.pkpass')
        return zipfile.ZipFile(io.BytesIO(resp.content))

    def test_apple_pass_contents_and_signature(self):
        z = self.pkpass()
        names = set(z.namelist())
        self.assertTrue({'pass.json', 'manifest.json', 'signature', 'icon.png', 'logo.png'} <= names)
        data = json.loads(z.read('pass.json'))
        self.assertEqual(data['passTypeIdentifier'], 'pass.com.arcu.attnd.tickets')
        self.assertEqual(data['teamIdentifier'], 'GQQX4L6TE2')
        self.assertEqual(data['webServiceURL'], 'https://attnd.app/wallet/')
        self.assertEqual(data['barcodes'][0]['message'], signing.token_for(self.ticket))
        self.assertEqual(data['eventTicket']['secondaryFields'][1]['value'], 'Ada Holder')
        self.assertEqual(data['locations'][0]['latitude'], 32.75)
        manifest = json.loads(z.read('manifest.json'))
        import hashlib
        for name, digest in manifest.items():
            self.assertEqual(hashlib.sha1(z.read(name)).hexdigest(), digest)
        if shutil.which('openssl'):
            with tempfile.TemporaryDirectory() as d:
                Path(d, 'manifest.json').write_bytes(z.read('manifest.json'))
                Path(d, 'signature').write_bytes(z.read('signature'))
                Path(d, 'ca.pem').write_text(pem(WWDR))
                result = subprocess.run(['openssl', 'smime', '-verify', '-binary', '-inform', 'DER', '-in',
                                         f'{d}/signature', '-content', f'{d}/manifest.json', '-CAfile', f'{d}/ca.pem',
                                         '-purpose', 'any', '-out', '/dev/null'], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)

    @override_settings(TICKETS_DEV_TOOLS=True)
    def test_dev_label(self):
        data = json.loads(self.pkpass().read('pass.json'))
        self.assertEqual(data['eventTicket']['headerFields'][0]['value'], 'DEV')

    def test_web_service_register_list_fetch_unregister(self):
        self.pkpass()
        wp = WalletPass.objects.get(ticket=self.ticket, platform='apple')
        url = f'/wallet/v1/devices/dev123/registrations/pass.com.arcu.attnd.tickets/{wp.serial}'
        auth = {'HTTP_AUTHORIZATION': f'ApplePass {wp.auth_token}'}
        self.assertEqual(self.client.post(url, json.dumps({'pushToken': 'abc'}), content_type='application/json',
                                          secure=True).status_code, 401)
        self.assertEqual(self.client.post(url, json.dumps({'pushToken': 'abc'}), content_type='application/json',
                                          secure=True, **auth).status_code, 201)
        self.assertEqual(self.client.post(url, json.dumps({'pushToken': 'abc'}), content_type='application/json',
                                          secure=True, **auth).status_code, 200)
        listing = self.client.get('/wallet/v1/devices/dev123/registrations/pass.com.arcu.attnd.tickets', secure=True)
        self.assertEqual(listing.json()['serialNumbers'], [wp.serial])
        tag = listing.json()['lastUpdated']
        self.assertEqual(self.client.get(f'/wallet/v1/devices/dev123/registrations/pass.com.arcu.attnd.tickets'
                                         f'?passesUpdatedSince={tag}', secure=True).status_code, 204)
        latest = self.client.get(f'/wallet/v1/passes/pass.com.arcu.attnd.tickets/{wp.serial}', secure=True, **auth)
        self.assertEqual(latest.status_code, 200)
        with patch('tickets.wallet_apple.push', return_value=[]) as push:
            with self.captureOnCommitCallbacks(execute=True):
                holders.transfer(self.ticket, 'other@example.com')
        push.assert_called_with(['abc'])  # the old holder's pass is told to update…
        old = json.loads(zipfile.ZipFile(io.BytesIO(self.client.get(
            f'/wallet/v1/passes/pass.com.arcu.attnd.tickets/{wp.serial}', secure=True, **auth).content)).read(
            'pass.json'))
        self.assertTrue(old['voided'])  # …and comes back void
        self.assertEqual(self.client.delete(url, secure=True, **auth).status_code, 200)
        self.assertFalse(WalletRegistration.objects.exists())
        self.assertEqual(self.client.post('/wallet/v1/log', json.dumps({'logs': ['x']}),
                                          content_type='application/json', secure=True).status_code, 200)

    def test_google_save_link(self):
        resp = self.client.get(f'/tickets/ticket/{self.ticket.public_id}/{self.ticket.key}/google-wallet/', secure=True)
        self.assertTrue(resp['Location'].startswith('https://pay.google.com/gp/v/save/'))
        token = resp['Location'].rsplit('/', 1)[1]
        claims = jwt.decode(token, GOOGLE_KEY.public_key(), algorithms=['RS256'], audience='google')
        obj = claims['payload']['eventTicketObjects'][0]
        self.assertEqual(obj['barcode']['value'], signing.token_for(self.ticket))
        self.assertTrue(obj['classId'].startswith('3388000000012345678.attnd-event-'))
        self.assertEqual(obj['state'], 'ACTIVE')

    @override_settings(APPLE_WALLET_CERT_PEM='', GOOGLE_WALLET_ISSUER_ID='')
    def test_disabled_without_credentials(self):
        self.assertFalse(wallet.apple_enabled() or wallet.google_enabled())
        self.assertEqual(self.client.get(f'/tickets/ticket/{self.ticket.public_id}/{self.ticket.key}/wallet.pkpass',
                                         secure=True).status_code, 404)


@override_settings(**STRIPE_SETTINGS, SITE_URL='https://attnd.app', STUDIO_BETA_EMAILS=[])
class ConsoleTests(TestCase):
    def setUp(self):
        self.stripe = FakeStripe().install(self)
        self.owner = User.objects.create_user('owner', 'owner@example.com', 'pw')
        StudioAccess.objects.create(email='owner@example.com')
        self.event, self.ga, self.free = ticketed_event(owner=self.owner)
        self.client.force_login(self.owner)
        self.base = f'/console/events/{self.event.id}/tickets/'

    def test_gated_by_studio_access_and_plan(self):
        outsider = User.objects.create_user('x', 'x@example.com', 'pw')
        from events.models import EventMember
        EventMember.objects.create(event=self.event, user=outsider, role='admin')
        self.client.force_login(outsider)
        self.assertEqual(self.client.get(self.base, secure=True).status_code, 404)  # no Studio access
        self.client.force_login(self.owner)
        self.assertContains(self.client.get(self.base, secure=True), 'Tickets sold')
        self.event.plan = 'standard'
        self.event.save()
        self.assertContains(self.client.get(self.base, secure=True), 'Studio + tickets')
        self.assertRedirects(self.client.get(self.base + 'orders/', secure=True), self.base,
                             fetch_redirect_response=False)

    def test_pages_render(self):
        order = issue(self.event, self.free, email='g@example.com')[0].order
        for page in ('', 'types/', 'types/new/', f'types/{self.ga.id}/', 'promos/', 'settings/', 'orders/',
                     f'orders/{order.id}/', 'checkins/', 'door/', 'message/', 'payouts/', 'cancel/'):
            self.assertEqual(self.client.get(self.base + page, secure=True).status_code, 200, page)
        self.assertEqual(self.client.get('/console/tickets/demo/', secure=True).status_code, 404)  # staff only

    def test_ticket_type_form_in_dollars(self):
        resp = self.client.post(self.base + 'types/new/', {
            'name': 'VIP', 'description': '', 'kind': 'paid', 'price_cents': '75.00', 'quantity': '20',
            'min_per_order': 1, 'max_per_order': 4, 'sort_order': 3, 'transferable': 'on', 'waitlist': 'on'},
            secure=True)
        self.assertEqual(resp.status_code, 302, resp.content[:2000])
        self.assertEqual(TicketType.objects.get(name='VIP').price_cents, 7500)
        resp = self.client.post(self.base + 'types/new/', {'name': 'Bad', 'kind': 'paid', 'price_cents': '0',
                                                           'min_per_order': 1, 'max_per_order': 4, 'sort_order': 0},
                                secure=True)
        self.assertContains(resp, 'need a price')

    def test_refund_and_comp_and_csv(self):
        holders.comp(self.event, self.free, 2, 'press@example.com', '=HYPERLINK("x")')
        csv = self.client.get(self.base + 'attendees.csv', secure=True).content.decode()
        self.assertIn("'=HYPERLINK", csv)  # formulas are neutralised
        resp = self.client.post(self.base + 'comp/', {'ticket_type': self.free.id, 'quantity': 1,
                                                      'email': 'guest@example.com', 'name': 'G'}, secure=True)
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(Order.objects.filter(comp=True).count(), 2)

    def test_contributors_cannot_move_money(self):
        helper = User.objects.create_user('h', 'h@example.com', 'pw')
        StudioAccess.objects.create(email='h@example.com')
        from events.models import EventMember
        EventMember.objects.create(event=self.event, user=helper, role='contributor')
        self.client.force_login(helper)
        order = issue(self.event, self.free)[0].order
        self.client.post(self.base + f'orders/{order.id}/', {'action': 'refund_all'}, secure=True)
        self.assertEqual(Order.objects.get(pk=order.pk).status, Order.PAID)
        self.assertRedirects(self.client.get(self.base + 'settings/', secure=True), self.base,
                             fetch_redirect_response=False)

    def test_door_staff_invite(self):
        self.client.post(self.base + 'door/', {'email': 'Door@Example.com', 'name': 'Dee'}, secure=True)
        self.assertTrue(DoorStaff.objects.filter(email='door@example.com').exists())
        self.assertIn('door team', mail.outbox[-1].subject)

    def test_payouts_connect_redirects_to_stripe(self):
        TicketSettings.objects.filter(event=self.event).update(payout_account=None)
        resp = self.client.post(self.base + 'payouts/', {'action': 'connect'}, secure=True)
        self.assertTrue(resp['Location'].startswith('https://connect.stripe.com/'))
        self.client.get(self.base + 'payouts/?connect=return', secure=True)
        self.assertTrue(TicketSettings.objects.get(event=self.event).payout_account.ready)

    def test_cancel_needs_the_code(self):
        self.client.post(self.base + 'cancel/', {'confirm': 'nope'}, secure=True)
        self.assertFalse(TicketSettings.objects.get(event=self.event).cancelled)
        self.client.post(self.base + 'cancel/', {'confirm': 'tixfest', 'reason': 'Rain'}, secure=True)
        self.assertTrue(TicketSettings.objects.get(event=self.event).cancelled)

    def test_ai_draft_is_reviewed_and_money_needs_a_human(self):
        draft = {'ticket_types': [
            {'name': 'General Admission', 'description': 'Lawn seats.', 'kind': 'paid', 'price_dollars': 30,
             'quantity': 150, 'max_per_order': 10, 'add_on': False, 'reentry': False, 'transferable': True},
            {'name': 'VIP', 'description': 'Front rows.', 'kind': 'paid', 'price_dollars': 80, 'quantity': 20,
             'max_per_order': 4, 'add_on': False, 'reentry': False, 'transferable': True}],
            'page_text': 'See you there.', 'refund_policy_text': 'Refunds until a week before.', 'faq': '', 'notes': ''}
        with patch('tickets.ai._call_model', return_value=draft), \
                patch('scrape.jobs._start_background', side_effect=lambda jid: __import__('scrape.jobs').jobs.run_job(jid)):
            with self.captureOnCommitCallbacks(execute=True):
                resp = self.client.post(self.base + 'draft/', {'instructions': 'GA $30, VIP $80'}, secure=True)
        self.assertEqual(resp.status_code, 302, resp.content[:500])
        job = IngestJob.objects.get(kind=IngestJob.KIND_TICKETS)
        self.assertEqual(job.status, IngestJob.DONE, job.error)
        cs = ChangeSet.objects.get(pk=job.result['changeset_id'])
        update = cs.changes.get(entity='ticket_type', op='update')
        self.assertEqual(set(update.human_fields), {'price_cents', 'quantity'})
        create = cs.changes.get(entity='ticket_type', op='create')
        self.assertIn('price_cents', create.human_fields)
        page = self.client.get(f'/console/changes/{cs.id}/', secure=True).content.decode()
        self.assertIn('Money: needs your OK', page)
        self.assertIn(f'name="field_{update.id}_description" value="1" aria-label="Description" checked', page)
        self.assertNotIn(f'name="field_{update.id}_price_cents" value="1" aria-label="Price" checked', page)
        from scrape import diff
        decisions = diff.default_decisions(cs)  # what automatic paths would apply: no money, no new types
        self.assertNotIn(create.id, decisions)
        self.assertEqual(decisions[update.id]['fields'], ['description'])
        self.client.post(f'/console/changes/{cs.id}/', {f'accept_{update.id}': '1',
                                                        f'field_{update.id}_description': '1',
                                                        f'field_{update.id}_price_cents': '1',
                                                        f'accept_{create.id}': '1'}, secure=True)
        self.ga.refresh_from_db()
        self.assertEqual((self.ga.price_cents, self.ga.quantity, self.ga.description), (3000, 100, 'Lawn seats.'))
        self.assertEqual(TicketType.objects.get(name='VIP').price_cents, 8000)
        rev = self.event.revisions.first()  # history: roll back
        from events.content import restore
        restore(rev, self.owner)
        self.ga.refresh_from_db()
        self.assertEqual(self.ga.price_cents, 2500)
        self.assertFalse(TicketType.objects.filter(name='VIP').exists())


@override_settings(TICKETS_DEMO_EVENT_CODE='TIXDEMO', TICKETS_TEST_EMAIL_DOMAIN='ci.attnd.test')
class DemoFactsTests(TestCase):
    """The DEMO ticket facts the live suites (web, iOS, Android) assert. Change them in all three together."""

    def test_load_ticket_demo(self):
        call_command('load_ticket_demo', stdout=io.StringIO())
        call_command('load_ticket_demo', stdout=io.StringIO())  # idempotent reset, same event
        from events.models import Event
        event = Event.objects.get(event_code='TIXDEMO')
        self.assertEqual(event.plan, 'studio')
        types = list(TicketType.objects.filter(event=event).order_by('sort_order'))
        self.assertEqual([t.name for t in types], ['General Admission', 'VIP', 'Kids under 12', 'Support the artists',
                                                   'Parking', 'Backstage', 'Early Bird'])
        self.assertEqual([t.price_cents for t in types], [2500, 7500, 0, 500, 1000, 15000, 1500])
        rows = {r['type'].name: r for r in inventory.listing(event, ['BACKSTAGE'])}
        self.assertEqual(rows['Early Bird']['state'], 'sold_out')
        self.assertNotIn('Backstage', [r['type'].name for r in inventory.listing(event)])
        self.assertTrue(rows['Parking']['type'].add_on)
        self.assertEqual(Ticket.objects.filter(event=event, status=Ticket.VALID).count(), 14)
        self.assertEqual(Ticket.objects.filter(event=event, status=Ticket.REFUNDED).count(), 1)
        self.assertEqual(Ticket.objects.filter(event=event, checked_in_gate='Gate A').count(), 1)
        self.assertTrue(DoorStaff.objects.filter(event=event, email='door@ci.attnd.test').exists())
        self.assertEqual(event.promo_codes.get().code, 'SAVE10')
        self.assertTrue(inventory.paid_ready(event) is False)  # no Stripe key in tests
        self.assertEqual(inventory.sales_state(event)[0], 'open')


@override_settings(SITES_HOST='sites.example', **STRIPE_SETTINGS)
class StudioTicketsTests(TestCase):
    def test_site_data_has_tickets_with_all_in_prices(self):
        from studio import publishing
        from studio.sitedata import site_data
        event, ga, free = ticketed_event()
        site = publishing.ensure_site(event, event.user)
        data = site_data(site, 'https://sites.example/x/')
        self.assertTrue(data['tickets']['url'].endswith('/t/TIXFEST/?src=site'))
        prices = {t['name']: t['price_text'] for t in data['tickets']['types']}
        self.assertEqual(prices, {'General Admission': '$26.06', 'RSVP': 'Free'})
        js = (Path(__file__).resolve().parent.parent / 'studio' / 'site_static' / 'attnd.js').read_text()
        self.assertIn("define('attnd-tickets'", js)
        if shutil.which('node'):
            result = subprocess.run(['node', '--check', '-'], input=js, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
