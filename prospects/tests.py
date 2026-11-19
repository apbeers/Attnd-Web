"""Prospects (staff only): access, discovery with OpenAI and HTTP mocked, schedule, testing-only events, CSV."""

from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest import mock

from django.contrib.auth.models import User
from django.core import mail
from django.test import TestCase, override_settings
from django.utils import timezone

from console.testing import TwoFactorClient
from events.models import Event, EventInvite
from prospects import discover, schedule
from prospects.models import Prospect, ProspectContact, ProspectRun, ProspectSettings
from scrape import jobs
from scrape.models import IngestJob

PAGE = ('<html><body><h1>Deep Ellum Art Walk</h1><p>Galleries along Main St. Organized by the Deep Ellum Arts Guild. '
        'Director Jane Doe, jane@deepellumarts.org, (214) 555-0199.</p>'
        '<a href="/contact/">Contact us</a><a href="/tickets/">Tickets</a></body></html>')
CONTACT_PAGE = ('<html><body><p>Sponsorships: Sam Lee <a href="mailto:sponsor@deepellumarts.org">email</a></p>'
                '</body></html>')


def fake_fetch(url, limit):
    pages = {'https://deepellumarts.org/walk': PAGE, 'https://deepellumarts.org/contact/': CONTACT_PAGE,
             'https://grapevinefest.com/': '<html><body>Grapevine wine walk downtown</body></html>'}
    if url not in pages:
        from studio.importer import SiteImportError
        raise SiteImportError(f'Could not load {url}')
    return pages[url].encode(), 'text/html', url


def search_reply(*cands):
    return {'candidates': [{'name': c[0], 'urls': c[1], 'start_date': c[2] if len(c) > 2 else None,
                            'end_date': None, 'city': 'Dallas', 'venue': '', 'organizer': '', 'why': 'walkable'}
                           for c in cands]}


def enrich_reply(name='Deep Ellum Art Walk', score=88, contacts=None):
    return {'is_event': True, 'name': name, 'organizer': 'Deep Ellum Arts Guild', 'summary': 'An art walk.',
            'start_date': (timezone.localdate() + timedelta(days=20)).isoformat(), 'end_date': None,
            'city': 'Dallas', 'venue': 'Deep Ellum', 'fit_score': score,
            'fit_reasons': ['Outdoor', 'Many galleries'], 'traits': ['outdoor', 'multiple_stops', 'bogus'],
            'contacts': contacts if contacts is not None else [
                # on the page: kept
                {'name': 'Jane Doe', 'role': 'Director', 'organization': 'Deep Ellum Arts Guild',
                 'email': 'jane@deepellumarts.org', 'phone': '(214) 555-0199', 'website': '', 'profile_url': '',
                 'source_url': 'https://deepellumarts.org/walk'},
                # an invented email (not on the page): the person stays, the email is dropped
                {'name': 'Bob Guess', 'role': '', 'organization': 'Deep Ellum Arts Guild',
                 'email': 'bob@deepellumarts.org', 'phone': '214-555-0000', 'website': '', 'profile_url': '',
                 'source_url': 'https://deepellumarts.org/walk'},
                # a page we never fetched: dropped entirely
                {'name': 'Ghost', 'role': '', 'organization': '', 'email': 'ghost@example.com', 'phone': '',
                 'website': '', 'profile_url': '', 'source_url': 'https://elsewhere.example/about'},
                # from the followed contact page (mailto link)
                {'name': 'Sam Lee', 'role': 'Sponsorships', 'organization': '', 'email': 'sponsor@deepellumarts.org',
                 'phone': '', 'website': '', 'profile_url': '', 'source_url': 'https://deepellumarts.org/contact/'},
            ]}


class Mocked:
    """respond() replies in order; each call costs $0.01."""

    def __init__(self, *replies, cost='0.01'):
        self.replies = list(replies)
        self.calls = []
        self.cost = Decimal(cost)

    def __call__(self, instructions, user_input, schema, name, web_search=None):
        self.calls.append({'name': name, 'web_search': web_search, 'input': user_input})
        return self.replies.pop(0), self.cost, 'gpt-5.5'


def run_with(replies, cost='0.01', **kwargs):
    fake = Mocked(*replies, cost=cost)
    with mock.patch('prospects.discover.respond', fake), mock.patch('studio.importer.fetch', fake_fetch), \
            mock.patch('studio.importer._public_host', return_value=True):
        run = discover.execute(discover.create_run(ProspectRun.MANUAL).pk, **kwargs)
    return run, fake


class Base(TestCase):
    def setUp(self):
        conf = ProspectSettings.load()
        conf.themes = 'art walks'  # one search per run
        conf.save()
        self.staff = User.objects.create_user('staff', 'staff@example.com', 'pw', is_staff=True)
        self.organizer = User.objects.create_user('org', 'org@example.com', 'pw')
        self.client = TwoFactorClient()


class AccessTests(Base):
    def test_staff_only(self):
        p = Prospect.objects.create(name='X', urls=['https://x.example/'])
        run = ProspectRun.objects.create()
        urls = ['/console/prospects/', f'/console/prospects/{p.pk}/', '/console/prospects/runs/',
                f'/console/prospects/runs/{run.pk}/', '/console/prospects/settings/', '/console/prospects/export.csv']
        for url in urls:
            self.assertEqual(self.client.get(url, secure=True).status_code, 302, url)  # sign in first
        self.client.force_login(self.organizer)
        for url in urls:
            self.assertEqual(self.client.get(url, secure=True).status_code, 404, url)
        self.assertEqual(self.client.post('/console/prospects/runs/', secure=True).status_code, 404)
        self.client.force_login(self.staff)
        for url in urls:
            self.assertEqual(self.client.get(url, secure=True).status_code, 200, url)

    def test_topbar_button_for_staff_only(self):
        self.client.force_login(self.organizer)
        self.assertNotContains(self.client.get('/console/', secure=True), '/console/prospects/')
        self.client.force_login(self.staff)
        self.assertContains(self.client.get('/console/', secure=True), '/console/prospects/')

    def test_settings_form_saves(self):
        self.client.force_login(self.staff)
        resp = self.client.post('/console/prospects/settings/', {
            'region': 'Austin, Texas', 'timezone': 'America/Chicago', 'themes': 'art walks\nmarkets',
            'horizon_days': '60', 'max_candidates': '10', 'schedule_enabled': 'on', 'interval_days': '3'}, secure=True)
        self.assertEqual(resp.status_code, 302)
        conf = ProspectSettings.load()
        self.assertEqual((conf.region, conf.theme_list, conf.max_candidates, conf.schedule_enabled, conf.interval_days),
                         ('Austin, Texas', ['art walks', 'markets'], 10, True, 3))
        bad = self.client.post('/console/prospects/settings/', {'region': 'x', 'timezone': 'Mars/Base', 'themes': 'a',
                                                               'horizon_days': '1', 'max_candidates': '1',
                                                               'interval_days': '1'}, secure=True)
        self.assertContains(bad, 'IANA time zone')

    def test_run_now_starts_a_background_run(self):
        self.client.force_login(self.staff)
        with mock.patch('prospects.discover.threading.Thread') as thread:
            with self.captureOnCommitCallbacks(execute=True):
                resp = self.client.post('/console/prospects/runs/', secure=True)
        run = ProspectRun.objects.get()
        self.assertRedirects(resp, f'/console/prospects/runs/{run.pk}/', fetch_redirect_response=False)
        self.assertEqual((run.trigger, run.started_by, run.status), ('manual', self.staff, 'queued'))
        thread.return_value.start.assert_called_once()
        # A second press while it runs starts nothing.
        self.client.post('/console/prospects/runs/', secure=True)
        self.assertEqual(ProspectRun.objects.count(), 1)


class DiscoveryTests(Base):
    def test_new_prospect_with_contacts_from_the_pages_only(self):
        run, fake = run_with([search_reply(('Deep Ellum Art Walk', ['https://deepellumarts.org/walk'])),
                              enrich_reply()])
        self.assertEqual(run.status, 'done', run.error)
        self.assertEqual((run.found, run.new, run.updated, run.skipped), (1, 1, 0, 0))
        self.assertEqual(run.cost_usd, Decimal('0.02'))
        self.assertEqual(fake.calls[0]['web_search']['type'], 'web_search')
        self.assertIn('art walks', fake.calls[0]['input'])
        self.assertIn('## SOURCE: https://deepellumarts.org/contact/', fake.calls[1]['input'])  # followed one link
        p = Prospect.objects.get()
        self.assertEqual((p.name, p.fit_score, p.organizer, p.city), ('Deep Ellum Art Walk', 88, 'Deep Ellum Arts Guild', 'Dallas'))
        self.assertEqual(p.traits, ['outdoor', 'multiple_stops'])  # unknown traits dropped
        self.assertEqual(p.url_key, 'deepellumarts.org/walk')
        contacts = {c.name: c for c in p.contacts.all()}
        self.assertEqual(set(contacts), {'Jane Doe', 'Bob Guess', 'Sam Lee'})  # Ghost: no fetched source
        self.assertEqual((contacts['Jane Doe'].email, contacts['Jane Doe'].phone), ('jane@deepellumarts.org', '(214) 555-0199'))
        self.assertEqual((contacts['Bob Guess'].email, contacts['Bob Guess'].phone), ('', ''))  # never guessed
        self.assertEqual(contacts['Sam Lee'].email, 'sponsor@deepellumarts.org')
        self.assertTrue(all(c.source_url for c in contacts.values()))
        self.assertEqual(p.first_seen_run, run)

    def test_known_prospect_is_merged_and_keeps_human_edits(self):
        p = Prospect.objects.create(name='Deep Ellum Art Walk', urls=['https://deepellumarts.org/walk/'],
                                    url_key='deepellumarts.org/walk', status=Prospect.CONTACTED, notes='Called Jane')
        start = (timezone.localdate() + timedelta(days=30)).isoformat()
        run, fake = run_with([search_reply(('Deep Ellum Art Walk (Fall)',
                                            ['https://www.deepellumarts.org/walk', 'https://listing.example/e/1'],
                                            start))])
        self.assertEqual(len(fake.calls), 1)  # known: no page reading, no enrich call
        self.assertEqual((run.found, run.new, run.updated), (1, 0, 1))
        p.refresh_from_db()
        self.assertEqual((p.status, p.notes, p.name), (Prospect.CONTACTED, 'Called Jane', 'Deep Ellum Art Walk'))
        self.assertEqual(p.start_date.isoformat(), start)
        self.assertIn('https://listing.example/e/1', p.urls)
        self.assertEqual(Prospect.objects.count(), 1)

    def test_same_name_and_year_merges_into_one_prospect_and_adds_contacts(self):
        when = timezone.localdate() + timedelta(days=20)
        p = Prospect.objects.create(name='The Deep Ellum Art Walk 2026', start_date=when,
                                    name_key=discover.name_key('Deep Ellum Art Walk', when.year),
                                    urls=['https://other.example/x'], url_key='other.example/x', fit_score=70,
                                    fit_reasons=['ok'])
        run, _ = run_with([search_reply(('A new listing', ['https://deepellumarts.org/walk'])), enrich_reply()])
        self.assertEqual((run.new, run.updated), (0, 1))
        self.assertEqual(Prospect.objects.count(), 1)
        self.assertEqual(p.contacts.count(), 3)
        self.assertIn('https://deepellumarts.org/walk', Prospect.objects.get().urls)

    def test_existing_attnd_events_are_skipped(self):
        Event.objects.create(name='Already', event_code='ALRDY1', start_time=timezone.now(),
                             source_urls=['https://deepellumarts.org/walk/'])
        run, fake = run_with([search_reply(('Deep Ellum Art Walk', ['https://deepellumarts.org/walk']))])
        self.assertEqual((run.found, run.new, run.skipped), (1, 0, 1))
        self.assertEqual(len(fake.calls), 1)
        self.assertFalse(Prospect.objects.exists())

    def test_candidate_cap(self):
        run, fake = run_with([search_reply(('Deep Ellum Art Walk', ['https://deepellumarts.org/walk']),
                                           ('Grapevine Wine Walk', ['https://grapevinefest.com/'])),
                              enrich_reply()], max_new=1)
        self.assertEqual((run.new, Prospect.objects.count(), len(fake.calls)), (1, 1, 2))
        self.assertIn("run's cap", run.log)

    @override_settings(PROSPECT_DAILY_SPEND_CAP_USD=0.015)
    def test_spend_cap_stops_the_run(self):
        run, fake = run_with([search_reply(('Deep Ellum Art Walk', ['https://deepellumarts.org/walk'])),
                              enrich_reply()], cost='0.02')
        self.assertEqual(len(fake.calls), 1)  # the search used the budget up; nothing was read after it
        self.assertEqual(run.status, 'done')
        self.assertIn('budget', run.log)
        # A new run doesn't even search today.
        run2, fake2 = run_with([], cost='0.02')
        self.assertEqual(fake2.calls, [])
        self.assertIn('Not started', run2.log)

    def test_unreadable_page_is_skipped(self):
        run, fake = run_with([search_reply(('Nowhere Fest', ['https://unknown.example/']))])
        self.assertEqual((run.found, run.new, run.skipped, len(fake.calls)), (1, 0, 1, 1))

    def test_past_events_score_low(self):
        reply = enrich_reply()
        reply['start_date'] = (timezone.localdate() - timedelta(days=5)).isoformat()
        run_with([search_reply(('Deep Ellum Art Walk', ['https://deepellumarts.org/walk'])), reply])
        self.assertLessEqual(Prospect.objects.get().fit_score, 10)

    def test_ssrf_guard_applies(self):
        """Private addresses are refused by Studio's fetch (the guard is not mocked here)."""
        self.assertIsNone(discover.fetch_page('http://127.0.0.1/admin'))

    @mock.patch.dict('os.environ', {'OPENAI_API_KEY': 'sk-test'})
    def test_respond_counts_web_searches_and_falls_back_only_when_unavailable(self):
        import httpx
        from openai import NotFoundError
        request = httpx.Request('POST', 'https://api.openai.com/v1/responses')
        not_found = NotFoundError('The model `gpt-5.5` does not exist or you do not have access to it.', body=None,
                                  response=httpx.Response(404, request=request))
        reply = SimpleNamespace(
            output=[SimpleNamespace(type='web_search_call'), SimpleNamespace(type='web_search_call'),
                    SimpleNamespace(type='message')],
            output_text='```json\n{"candidates": []}\n```',
            usage=SimpleNamespace(input_tokens=1_000_000, output_tokens=0, input_tokens_details=None))
        client = mock.MagicMock()
        client.responses.create.side_effect = [not_found, reply]
        discover._unavailable_models.clear()
        with mock.patch('openai.OpenAI', return_value=client):
            data, cost, model = discover.respond('I', 'U', discover.SEARCH_SCHEMA, 'x', web_search={'type': 'web_search'})
        discover._unavailable_models.clear()
        self.assertEqual((data, model), ({'candidates': []}, 'gpt-5'))
        self.assertEqual(cost, Decimal('1.25') + Decimal('0.02'))  # gpt-5 input price + 2 searches
        kwargs = client.responses.create.call_args.kwargs
        self.assertEqual(kwargs['tools'], [{'type': 'web_search'}])
        self.assertEqual(kwargs['text']['format']['type'], 'json_schema')

    def test_name_and_url_keys(self):
        self.assertEqual(discover.url_key('https://WWW.Example.com/Events/Walk/?utm=1#x'), 'example.com/events/walk')
        self.assertEqual(discover.name_key('The 12th Annual Deep Ellum Arts Festival 2027', 2027),
                         'deep ellum arts festival|2027')
        self.assertEqual(discover.name_key('Deep Ellum Arts Festival', 2027), 'deep ellum arts festival|2027')


class ScheduleTests(Base):
    def test_gating(self):
        conf = ProspectSettings.load()
        self.assertFalse(schedule.is_due(conf))  # off by default
        conf.schedule_enabled, conf.interval_days = True, 7
        self.assertTrue(schedule.is_due(conf))  # never ran
        conf.last_run_at = timezone.now() - timedelta(days=3)
        self.assertFalse(schedule.is_due(conf))
        conf.last_run_at = timezone.now() - timedelta(days=7)
        self.assertTrue(schedule.is_due(conf))

    def test_maybe_run(self):
        self.assertIsNone(schedule.maybe_run())
        ProspectSettings.objects.update(schedule_enabled=True)
        with mock.patch('prospects.discover.respond', Mocked({'candidates': []})):
            run = schedule.maybe_run()
        self.assertEqual((run.trigger, run.status), ('scheduled', 'done'))
        self.assertIsNotNone(ProspectSettings.load().last_run_at)
        self.assertIsNone(schedule.maybe_run())  # not due again for 7 days

    def test_runs_never_overlap(self):
        busy = ProspectRun.objects.create(status=ProspectRun.RUNNING, started_at=timezone.now())
        run, fake = run_with([{'candidates': []}])
        self.assertEqual((run.status, fake.calls), ('skipped', []))
        self.assertIn(str(busy.pk), run.error)
        ProspectSettings.objects.update(schedule_enabled=True)
        self.assertIsNone(schedule.maybe_run())

    def test_stale_running_run_is_failed_and_a_new_one_goes(self):
        old = ProspectRun.objects.create(status=ProspectRun.RUNNING, started_at=timezone.now() - timedelta(hours=2))
        run, _ = run_with([{'candidates': []}])
        old.refresh_from_db()
        self.assertEqual((old.status, run.status), ('failed', 'done'))

    def test_run_scheduled_tasks_calls_it(self):
        from django.core.management import call_command
        with mock.patch('prospects.schedule.maybe_run', return_value=None) as maybe:
            call_command('run_scheduled_tasks', stdout=mock.MagicMock())
        maybe.assert_called_once()


IMPORTED = {
    'name': 'Deep Ellum Art Walk', 'description': 'Art.', 'address': 'Main St, Dallas, TX', 'latitude': 32.78,
    'longitude': -96.78, 'timezone': 'America/Chicago', 'start_time': '2026-12-12T18:00:00',
    'end_time': '2026-12-12T22:00:00', 'context': {}, 'tag_vocabulary': ['art'], 'notes': '',
    'locations': [{'existing_id': None, 'name': 'Gallery 1', 'snippet': '', 'description': '', 'category': 'Gallery',
                   'website': None, 'image_url': None, 'address': '', 'latitude': 32.781, 'longitude': -96.781,
                   'tags': ['art'], 'context': {}}],
    'schedule': [], 'removed_location_ids': [], 'removed_item_ids': [],
}


@override_settings(API_KEY='test-key')
class TestEventTests(Base):
    def setUp(self):
        super().setUp()
        self.prospect = Prospect.objects.create(name='Deep Ellum Art Walk', urls=['https://deepellumarts.org/walk'])
        ProspectContact.objects.create(prospect=self.prospect, name='Jane', email='jane@deepellumarts.org',
                                       source_url='https://deepellumarts.org/walk')
        self.client.force_login(self.staff)

    def _create(self, url=None, data=None):
        with mock.patch('scrape.jobs._start_background', side_effect=lambda jid: jobs.run_job(jid)), \
                mock.patch('scrape.jobs.scrape_event', return_value=dict(IMPORTED)), \
                mock.patch('scrape.watch.sync_pages'):
            with self.captureOnCommitCallbacks(execute=True):
                return self.client.post(url or f'/console/prospects/{self.prospect.pk}/',
                                        data or {'action': 'create_test_event'}, secure=True)

    def test_create_test_event(self):
        resp = self._create()
        job = IngestJob.objects.get()
        self.assertRedirects(resp, f'/console/jobs/{job.pk}/', fetch_redirect_response=False)
        self.assertEqual((job.created_by, job.testing_only, job.studio_import, job.trigger, job.status),
                         (self.staff, True, False, 'prospect', 'done'))
        self.assertEqual(job.urls, ['https://deepellumarts.org/walk'])
        event = job.event
        self.assertEqual((event.testing_only, event.public, event.paid, event.status, event.refresh_schedule, event.user),
                         (True, False, False, Event.STATUS_PUBLISHED, Event.REFRESH_OFF, self.staff))
        self.prospect.refresh_from_db()
        self.assertEqual((self.prospect.event, self.prospect.status), (event, Prospect.TEST_EVENT))
        # Nobody was invited or emailed (least of all the organizer).
        self.assertEqual(mail.outbox, [])
        self.assertFalse(EventInvite.objects.exists())
        self.assertEqual(list(event.members.all()), [])
        # Never listed in nearby, even if someone ticks "public" later; lookup by code works.
        Event.objects.filter(pk=event.pk).update(public=True, start_time=timezone.now() + timedelta(hours=2))
        auth = {'HTTP_AUTHORIZATION': 'Bearer test-key'}
        nearby = self.client.get('/api/v2/events/nearby/?lat=32.78&lng=-96.78', secure=True, **auth).json()
        self.assertEqual(nearby['count'], 0)
        found = self.client.get(f'/api/v2/events/lookup/?q={event.event_code}', secure=True, **auth)
        self.assertEqual(found.status_code, 200)
        bundle = self.client.get(f'/api/v2/events/{event.pk}/bundle/', secure=True, **auth).json()
        self.assertNotIn('testing_only', str(bundle))
        # Not watched, and the console says what it is.
        from scrape import watch
        self.assertNotIn(event, list(watch.due_events(timezone.now())))
        self.assertContains(self.client.get(f'/console/events/{event.pk}/', secure=True), 'Testing only')
        # A second request is refused.
        self._create()
        self.assertEqual(IngestJob.objects.count(), 1)

    def test_bulk_create_and_status(self):
        other = Prospect.objects.create(name='No link')
        resp = self._create('/console/prospects/', {'action': 'create_test_events',
                                                    'ids': [self.prospect.pk, other.pk]})
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(IngestJob.objects.filter(testing_only=True).count(), 1)
        self.client.post('/console/prospects/', {'action': 'status', 'status': 'contacted', 'ids': [other.pk]},
                         secure=True)
        self.client.post('/console/prospects/', {'action': 'dismiss', 'ids': [self.prospect.pk]}, secure=True)
        other.refresh_from_db()
        self.prospect.refresh_from_db()
        self.assertEqual((other.status, self.prospect.status), ('contacted', 'dismissed'))

    def test_detail_actions(self):
        contact = self.prospect.contacts.get()
        url = f'/console/prospects/{self.prospect.pk}/'
        self.client.post(url, {'action': 'dnc', 'contact': contact.pk}, secure=True)
        contact.refresh_from_db()
        self.assertTrue(contact.do_not_contact)
        page = self.client.get(url, secure=True)
        self.assertContains(page, 'Do not contact')
        self.assertNotContains(page, 'data-copy="jane@deepellumarts.org"')
        self.client.post(url, {'action': 'save', 'status': 'interested', 'notes': 'Call back in May'}, secure=True)
        self.prospect.refresh_from_db()
        self.assertEqual((self.prospect.status, self.prospect.notes), ('interested', 'Call back in May'))
        self.assertTrue(self.prospect.activity.filter(text__startswith='Status').exists())

    def test_list_filters(self):
        Prospect.objects.create(name='Plano Balloon Festival', city='Plano', fit_score=30)
        self.prospect.fit_score = 90
        self.prospect.save()
        page = self.client.get('/console/prospects/?min_fit=50', secure=True)
        self.assertContains(page, 'Deep Ellum Art Walk')
        self.assertNotContains(page, 'Plano Balloon')
        page = self.client.get('/console/prospects/?q=jane&contacts=1', secure=True)
        self.assertContains(page, 'Deep Ellum Art Walk')
        self.assertNotContains(page, 'Plano Balloon')


class CsvTests(Base):
    def test_csv_is_formula_safe_and_honours_do_not_contact(self):
        p = Prospect.objects.create(name='=HYPERLINK("http://evil")', organizer='+cmd', urls=['https://a.example/'])
        ProspectContact.objects.create(prospect=p, name='@Ann', email='ann@a.example', phone='-1',
                                       source_url='https://a.example/')
        ProspectContact.objects.create(prospect=p, name='Private', email='no@a.example', phone='555',
                                       source_url='https://a.example/', do_not_contact=True)
        Prospect.objects.create(name='No contacts yet')
        self.client.force_login(self.staff)
        resp = self.client.get('/console/prospects/export.csv', secure=True)
        self.assertEqual(resp['Content-Type'], 'text/csv; charset=utf-8')
        body = resp.content.decode()
        self.assertIn("'=HYPERLINK", body)
        self.assertIn("'+cmd", body)
        self.assertIn("'@Ann", body)
        self.assertIn('ann@a.example', body)
        self.assertNotIn('no@a.example', body)
        self.assertIn('No contacts yet', body)
        self.assertEqual(views_csv_safe('-5'), "'-5")


def views_csv_safe(value):
    from prospects.views import csv_safe
    return csv_safe(value)
