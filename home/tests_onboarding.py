"""Onboarding (Nov 21, 2026): the home page (public, CSP, composer, chips, phone, gallery, SEO), the pending import
through sign-up, the console's first screen for people with no events (and the staff preview), the showcase loader,
Happening near you, Claim it (staff approval, hand-over, history) and the anonymous counts."""

import json
import re
from datetime import timedelta

from django.contrib.admin.models import LogEntry
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from events import showcase
from events.models import Event, Location
from home.models import HomeMetric, PendingImport
from scrape.models import IngestJob


@override_settings(SECURE_SSL_REDIRECT=False, INGEST_RUN_INLINE=False)
class HomePageTests(TestCase):
    def setUp(self):
        from django.core.cache import caches
        caches['local'].clear()  # the gallery is cached a couple of minutes per process

    def test_public_with_composer_chips_phone_gallery_and_seo(self):
        resp = self.client.get('/home/', secure=True)
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode()
        for part in ('data-composer', 'Build my event', 'data-chip="website"', 'data-look-screen="festival"',
                     'id="see-it-live"', 'og:image', 'application/ld+json', 'twitter:card'):
            self.assertIn(part, html, part)
        # CSP: every inline script carries the request's nonce.
        nonce = re.search(r"'nonce-([^']+)'", resp['Content-Security-Policy']).group(1)
        for tag in re.findall(r'<script(?![^>]*\bsrc=)[^>]*>', html):
            self.assertIn(f'nonce="{nonce}"', tag)
        data = json.loads(re.search(r'<script type="application/ld\+json"[^>]*>(.*?)</script>', html, re.S).group(1))
        self.assertEqual({d['@type'] for d in data}, {'Organization', 'WebSite', 'SoftwareApplication'})

    def test_gallery_shows_showcase_events_and_demo(self):
        with override_settings(SHOWCASE_EVENTS=True):
            showcase.load_all()
        Event.objects.create(name='Fort Worth Fest', event_code='DEMO', status=Event.STATUS_PUBLISHED, timezone='UTC', start_time=timezone.now())
        html = self.client.get('/home/', secure=True).content.decode()
        self.assertIn('Riverbend Music Festival', html)
        self.assertIn('Fort Worth Fest', html)
        self.assertIn(reverse('join:join_qr', args=['SHOWFEST']), html)
        self.assertIn('data-kind-filter="Market"', html)
        self.assertIn('attnd://join/SHOWFEST', html)

    def test_composer_keeps_it_until_sign_up_then_imports(self):
        resp = self.client.post(reverse('home:start'), {'text': 'A market in the park on Saturday', 'time_zone': 'America/Chicago'},
                                secure=True)
        self.assertRedirects(resp, reverse('console:signup') + '?building=1', fetch_redirect_response=False)
        pending = PendingImport.objects.get()
        self.assertEqual(pending.text, 'A market in the park on Saturday')
        self.assertEqual(HomeMetric.objects.get(name='submit').count, 1)
        signup = self.client.post(reverse('console:signup'), {'email': 'new@example.com', 'password': 'a-Long-pass-123',
                                                               'password2': 'a-Long-pass-123'}, secure=True)
        self.assertEqual(signup.status_code, 302, signup.content[:500])
        resp = self.client.get(reverse('console:dashboard'), secure=True)
        job = IngestJob.objects.get(trigger=IngestJob.TRIGGER_HOME)
        self.assertRedirects(resp, reverse('console:job', args=[job.pk]), fetch_redirect_response=False)
        self.assertEqual(job.created_by.email, 'new@example.com')
        self.assertEqual(job.text, 'A market in the park on Saturday')
        pending.refresh_from_db()
        self.assertIsNotNone(pending.used_at)
        # Used once only.
        self.assertEqual(self.client.get(reverse('console:dashboard'), secure=True).status_code, 200)
        self.assertEqual(IngestJob.objects.filter(trigger=IngestJob.TRIGGER_HOME).count(), 1)

    def test_attachments_are_checked_by_their_bytes(self):
        bad = SimpleUploadedFile('flyer.pdf', b'\x00\x01\x02 not a pdf \xff\xfe', content_type='application/pdf')
        self.client.post(reverse('home:start'), {'text': '', 'upload': bad}, secure=True)
        self.assertFalse(PendingImport.objects.exists())
        good = SimpleUploadedFile('flyer.pdf', b'%PDF-1.7 hello', content_type='application/pdf')
        self.client.post(reverse('home:start'), {'text': '', 'upload': good}, secure=True)
        self.assertEqual(PendingImport.objects.get().upload_type, 'application/pdf')
        self.client.post(reverse('home:start'), {'text': '   '}, secure=True)
        self.assertEqual(PendingImport.objects.count(), 1, 'nothing to build: nothing kept')

    def test_new_accounts_have_a_daily_import_cap(self):
        user = User.objects.create_user('n', 'n@example.com', 'pw')
        for _ in range(3):
            IngestJob.objects.create(kind=IngestJob.KIND_CREATE, created_by=user)
        c = Client()
        c.force_login(user)
        c.post(reverse('home:start'), {'text': 'Another event'}, secure=True)
        resp = c.get(reverse('console:dashboard'), secure=True)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'three events a day')

    def test_metrics_are_anonymous_counts(self):
        for _ in range(2):
            self.client.post(reverse('home:metric'), json.dumps({'name': 'chip', 'detail': 'Market!'}),
                             content_type='application/json', secure=True)
        self.client.post(reverse('home:metric'), json.dumps({'name': 'secret', 'detail': 'x'}),
                         content_type='application/json', secure=True)
        row = HomeMetric.objects.get()
        self.assertEqual((row.name, row.detail, row.count), ('chip', 'market', 2))


@override_settings(SECURE_SSL_REDIRECT=False)
class ConsoleOnboardingTests(TestCase):
    def test_no_events_shows_the_hero_and_gallery(self):
        user = User.objects.create_user('a', 'a@example.com', 'pw')
        c = Client()
        c.force_login(user)
        html = c.get(reverse('console:dashboard'), secure=True).content.decode()
        self.assertIn('data-composer', html)
        self.assertIn('Try one on your phone first', html)
        Event.objects.create(name='Mine', event_code='MINE1', user=user, timezone='UTC', start_time=timezone.now())
        html = c.get(reverse('console:dashboard'), secure=True).content.decode()
        self.assertNotIn('data-composer', html)
        self.assertIn('component--event_tile', html)

    def test_staff_preview(self):
        from console.testing import TwoFactorClient
        staff = User.objects.create_user('s', 's@example.com', 'pw', is_staff=True)
        Event.objects.create(name='Theirs', event_code='THEIRS', user=staff, timezone='UTC', start_time=timezone.now())
        c = TwoFactorClient()
        c.force_login(staff)
        html = c.get(reverse('console:dashboard') + '?onboarding=1', secure=True).content.decode()
        self.assertIn('Preview: what an organizer with no events sees', html)
        self.assertIn('Preview onboarding', html)
        user = User.objects.create_user('u', 'u@example.com', 'pw')
        Event.objects.create(name='Own', event_code='OWN1', user=user, timezone='UTC', start_time=timezone.now())
        u = Client()
        u.force_login(user)
        self.assertNotIn('data-composer', u.get(reverse('console:dashboard') + '?onboarding=1', secure=True).content.decode())

    def test_chat_default_on_for_new_accounts_and_the_migration(self):
        from importlib import import_module

        from django.apps import apps

        from console.models import ConsolePreference
        user = User.objects.create_user('c', 'c@example.com', 'pw')
        with override_settings(CHAT_DEFAULT_ON=True):
            self.assertTrue(ConsolePreference.chat_first(user, False))
            ConsolePreference.objects.create(user=user, chat_default=False)
            import_module('console.migrations.0014_chat_default_on').turn_on(apps, None)
            self.assertTrue(ConsolePreference.objects.get(user=user).chat_default)
        self.assertFalse(ConsolePreference(user=user).chat_default, 'tests default to the pages')


class ShowcaseTests(TestCase):
    def test_loader_is_idempotent_and_keeps_them_now(self):
        events = showcase.load_all()
        self.assertEqual(len(events), len(showcase.SHOWCASE))
        ids = {e.event_code: e.pk for e in events}
        again = showcase.load_all()
        self.assertEqual({e.event_code: e.pk for e in again}, ids)
        fest = Event.objects.get(event_code='SHOWFEST')
        self.assertTrue(fest.showcase and not fest.public and not fest.paid)
        self.assertEqual(fest.timing, 'now')
        self.assertEqual(fest.location_set.count(), 6)
        self.assertEqual(fest.notification_set.filter(sent=True).count(), 2)
        with override_settings(SHOWCASE_EVENTS=True):
            Event.objects.filter(pk=fest.pk).update(end_time=timezone.now() + timedelta(hours=2))
            self.assertGreaterEqual(showcase.refresh(), 1)
        self.assertGreater(Event.objects.get(pk=fest.pk).end_time, timezone.now() + timedelta(hours=10))

    def test_enabled_only_on_dev_by_default(self):
        with override_settings(SHOWCASE_EVENTS=None, DEBUG=False, SITE_URL='https://attnd.app'):
            self.assertFalse(showcase.enabled())
        with override_settings(SHOWCASE_EVENTS=None, DEBUG=False, SITE_URL='https://dev.attnd.app'):
            self.assertTrue(showcase.enabled())
        with override_settings(SHOWCASE_EVENTS=True, DEBUG=False, SITE_URL='https://attnd.app'):
            self.assertTrue(showcase.enabled())

    def test_not_nearby_and_no_usage(self):
        from events.apis import nearby_events
        e = showcase.load(showcase.SHOWCASE[0])
        Event.objects.filter(pk=e.pk).update(public=True)
        self.assertNotIn(e.pk, [x.pk for x, _, _ in nearby_events(e.latitude, e.longitude)])


@override_settings(SECURE_SSL_REDIRECT=False)
class ClaimTests(TestCase):
    def setUp(self):
        from django.core.cache import caches
        caches['local'].clear()
        from prospects.models import Prospect, ProspectContact
        self.staff = User.objects.create_user('staff', 'staff@example.com', 'pw', is_staff=True)
        now = timezone.now()
        self.event = Event.objects.create(name='Riverside Chili Cookoff', event_code='CHILI1', user=self.staff,
                                          status=Event.STATUS_PUBLISHED, testing_only=True, timezone='UTC',
                                          start_time=now + timedelta(days=3), end_time=now + timedelta(days=3, hours=8),
                                          latitude=32.75, longitude=-97.33, source_urls=['https://www.chilicookoff.org/'])
        Location.objects.create(event=self.event, name='Tent', latitude=32.75, longitude=-97.33)
        self.prospect = Prospect.objects.create(name='Riverside Chili Cookoff', event=self.event,
                                                urls=['https://www.chilicookoff.org/'], status=Prospect.TEST_EVENT)
        ProspectContact.objects.create(prospect=self.prospect, name='Pat', email='pat@chilicookoff.org',
                                       source_url='https://www.chilicookoff.org/contact')
        self.user = User.objects.create_user('pat', 'pat@chilicookoff.org', 'pw')

    def test_card_near_you_then_claim_needs_staff_approval(self):
        html = self.client.get('/home/', secure=True).content.decode()
        self.assertIn('Riverside Chili Cookoff', html)
        self.assertIn('Claim it', html)
        near = self.client.get(reverse('home:near') + '?lat=32.7&lng=-97.3', secure=True).json()
        self.assertEqual(near['count'], 1)
        far = self.client.get(reverse('home:near') + '?lat=47.6&lng=-122.3', secure=True).json()
        self.assertEqual(far['count'], 0)
        url = reverse('home:claim', args=['chili1'])
        self.assertEqual(self.client.get(url, secure=True).status_code, 302, 'a sign-in first')
        c = Client()
        c.force_login(self.user)
        resp = c.post(url, {'email': 'pat@chilicookoff.org', 'role': 'Organizer'}, secure=True)
        self.assertEqual(resp.status_code, 302)
        from prospects.models import ClaimRequest, Prospect
        claim = ClaimRequest.objects.get()
        self.assertEqual(claim.status, 'pending')
        self.assertIn('chilicookoff.org', claim.domain_match)
        self.event.refresh_from_db()
        self.assertEqual(self.event.user, self.staff, 'never automatic')
        # Staff approve in Prospects → Claims: the event is handed over, the prospect is Claimed, History records it.
        from console.testing import TwoFactorClient
        sc = TwoFactorClient()
        sc.force_login(self.staff)
        self.assertContains(sc.get(reverse('prospects:claims'), secure=True), 'pat@chilicookoff.org')
        sc.post(reverse('prospects:claims'), {'claim': claim.pk, 'action': 'approve'}, secure=True)
        self.event.refresh_from_db()
        self.assertEqual(self.event.user, self.user)
        self.assertFalse(self.event.testing_only)
        self.assertFalse(self.event.paid, 'the free limit stays until paid')
        self.assertEqual(Prospect.objects.get(pk=self.prospect.pk).status, Prospect.CLAIMED)
        self.assertTrue(LogEntry.objects.filter(object_id=str(self.event.pk)).exists())
        # Claimed events leave the near-you cards.
        self.assertEqual(self.client.get(reverse('home:near') + '?lat=32.7&lng=-97.3', secure=True).json()['count'], 0)

    def test_others_cannot_approve(self):
        from prospects.models import ClaimRequest
        claim = ClaimRequest.objects.create(event=self.event, prospect=self.prospect, user=self.user,
                                            email='pat@chilicookoff.org', role='Organizer')
        c = Client()
        c.force_login(self.user)
        self.assertEqual(c.post(reverse('prospects:claims'), {'claim': claim.pk, 'action': 'approve'}, secure=True).status_code, 404)
        claim.refresh_from_db()
        self.assertEqual(claim.status, 'pending')
