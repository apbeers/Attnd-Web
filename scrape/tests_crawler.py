"""Batch 7 A: the importer's crawl (scrape.crawler) and the detail-fill pass (scrape.detail_fill), with HTTP and
Claude mocked. The 2VLQITGR shape: a lineup page with names, times and "Event Details" links to one page per
performer, listed in the sitemap too."""
import io
import json
from datetime import timedelta
from unittest import mock

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from events.models import Event, Location, ScheduledItem
from scrape import crawler, detail_fill
from scrape.models import IngestJob

NAV = '<nav><a href="/">Home</a><a href="/lineup/">Lineup</a><a href="/privacy/">Privacy</a></nav>'
FOOT = '<footer><p>© Near Southside Inc · All rights reserved</p><p>Sign up for our newsletter</p></footer>'


def page(body):
    return f'<html><head><title>x</title></head><body>{NAV}{body}{FOOT}</body></html>'.encode()


def png(w, h):
    from PIL import Image
    buf = io.BytesIO()
    Image.new('RGB', (w, h), 'white').save(buf, 'PNG')
    return buf.getvalue()


SITE = {
    'https://fest.example/': ('text/html', page('<h1>Third Thursday</h1><a href="/lineup/">See the lineup</a>'
                                                '<div class="tribe-events"></div><link href="/wp-content/x.css">')),
    'https://fest.example/lineup/': ('text/html', page(
        '<h2>Lineup</h2><p>Allyn Fincher · 5 PM</p><a href="/artists/allyn-fincher/">Event Details</a>'
        '<p>Amelia Rose · 6 PM</p><a href="/x?id=2">Event Details</a>'
        '<a href="/files/full-schedule.pdf">Download the schedule (PDF)</a><a href="/files/annual-report.pdf">Report</a>'
        '<img src="/img/lineup-poster.png"><img src="/img/tiny.png"><img src="/img/Verizon_logo.png">')),
    'https://fest.example/artists/allyn-fincher/': ('text/html', page(
        '<h1>Allyn Fincher</h1><p>Painter of Texas skies. Original prints from $40.</p>')),
    'https://fest.example/x?id=2': ('text/html', page('<h1>Amelia Rose</h1><p>Folk singer from Denton.</p>')),
    'https://fest.example/artists/cole-gunter/': ('text/html', page('<h1>Cole Gunter</h1><p>Woodworker.</p>')),
    'https://fest.example/privacy/': ('text/html', page('<p>Privacy policy</p>')),
    'https://fest.example/sitemap.xml': ('application/xml', b'<urlset><url><loc>https://fest.example/artists/cole-gunter/'
                                                           b'</loc></url><url><loc>https://fest.example/about/</loc></url></urlset>'),
    'https://fest.example/robots.txt': ('text/plain', b'User-agent: *\n'),
    'https://fest.example/files/full-schedule.pdf': ('application/pdf', b'%PDF-1.4 schedule'),
    'https://fest.example/img/lineup-poster.png': ('image/png', png(900, 1200)),
    'https://fest.example/img/tiny.png': ('image/png', png(40, 40)),
    'https://fest.example/img/Verizon_logo.png': ('image/png', png(1600, 1600)),
    'https://fest.example/wp-json/tribe/events/v1/events?per_page=50': (
        'application/json', json.dumps({'events': [{'title': 'Gallery night', 'start_date': '2026-11-19 17:00:00',
                                                    'secret': 'x'}]}).encode()),
}


def fake_fetch(site=SITE):
    calls = []

    def fetch(url, limit):
        calls.append(url)
        if url not in site:
            raise OSError('404')
        ctype, body = site[url]
        return body, ctype, url
    fetch.calls = calls
    return fetch


class CrawlerTests(TestCase):
    def test_detail_pages_pdfs_images_and_embedded_data(self):
        fetch = fake_fetch()
        run = crawler.Crawl(fetch=fetch).run(['https://fest.example/'])
        visited = [u for _, u, _ in run.pages]
        for url in ('https://fest.example/artists/allyn-fincher/', 'https://fest.example/x?id=2',
                    'https://fest.example/artists/cole-gunter/'):
            self.assertIn(url, visited)  # "Event Details" links and the sitemap's detail pages
        self.assertNotIn('https://fest.example/privacy/', fetch.calls)  # skipped words never fetched
        names = [a['name'] for a in run.attachments]
        self.assertIn('full-schedule.pdf', names)
        self.assertNotIn('annual-report.pdf', names)  # a PDF without schedule words isn't read
        self.assertIn('lineup-poster.png', names)  # the big image of the lineup page, not the tiny one
        self.assertNotIn('tiny.png', names)
        self.assertNotIn('Verizon_logo.png', names)  # sponsor logos aren't schedules
        text = run.text()
        self.assertIn('Gallery night', text)  # The Events Calendar
        self.assertNotIn('secret', text)
        self.assertEqual(text.count('All rights reserved'), 1)  # boilerplate kept once
        self.assertLess(text.index('## Source: https://fest.example/lineup/'),
                        text.index('## Source: https://fest.example/artists/allyn-fincher/'))  # listings first

    def test_one_budget_for_every_source_url_and_no_page_twice(self):
        fetch = fake_fetch()
        crawler.Crawl(fetch=fetch, max_pages=3).run(['https://fest.example/', 'https://fest.example/lineup/'])
        pages = [u for u in fetch.calls if u.startswith('https://fest.example/') and not u.endswith(('.xml', '.txt'))
                 and '/files/' not in u and '/img/' not in u and 'wp-json' not in u]
        self.assertLessEqual(len(pages), 3)
        self.assertEqual(len(pages), len(set(pages)))

    def test_a_big_other_site_never_starves_the_organizer_site(self):
        """2VLQITGR on dev: the city's news site (pages full of "event / info / program") took the whole budget and the
        market's /entertainment/ page with the 43 performers was never read."""
        site = dict(SITE)
        site['https://fest.example/'] = ('text/html', page('<a href="/entertainment/">Entertainment</a>'))
        site['https://fest.example/entertainment/'] = ('text/html', page('<p>Swiss Longhorns · Norris Perry</p>'))
        city = ''.join(f'<a href="/News-Events/info-{i}">Event info {i}</a>' for i in range(60))
        site['https://city.example/news'] = ('text/html', page(city))
        for i in range(60):
            site[f'https://city.example/News-Events/info-{i}'] = ('text/html', page(f'<p>City program {i}</p>'
                                                                                 '<a href="/maps.pdf">maps</a>'))
        site['https://city.example/maps.pdf'] = ('application/pdf', b'%PDF-1.4 maps')
        run = crawler.Crawl(fetch=fake_fetch(site), max_pages=20).run(['https://fest.example/', 'https://city.example/news'])
        visited = [u for _, u, _ in run.pages]
        self.assertIn('https://fest.example/entertainment/', visited)
        self.assertLessEqual(sum(1 for u in visited if 'city.example' in u), crawler.OTHER_HOST_PAGES)
        self.assertNotIn('maps.pdf', [a['name'] for a in run.attachments])  # PDFs only from the organizer's site
        self.assertIn('Swiss Longhorns', run.text())

    def test_watch_mode_reads_only_the_pages(self):
        fetch = fake_fetch()
        run = crawler.Crawl(fetch=fetch).run(['https://fest.example/lineup/'], crawl=False)
        self.assertEqual([u for _, u, _ in run.pages], ['https://fest.example/lineup/'])
        self.assertFalse(run.attachments)

    def test_thin_js_page_keeps_its_embedded_data(self):
        html = ('<html><body><div id="__next"></div><script id="__NEXT_DATA__" type="application/json">'
                '{"props":{"lineup":[{"name":"Prairie Lights","time":"19:00"}]}}</script></body></html>')
        self.assertIn('Prairie Lights', crawler.page_text(html))
        self.assertTrue(crawler.is_detail_url('https://x.example/speakers/jane-doe/'))
        self.assertFalse(crawler.is_detail_url('https://x.example/speakers/'))

    def test_every_fetch_is_ssrf_guarded(self):
        from studio.importer import SiteImportError
        with self.assertRaises(SiteImportError):
            crawler.default_fetch('http://127.0.0.1/admin', 1000)


ANSWER = {'items': [{'ref': 's0', 'context': {'about': 'Painter of Texas skies.', 'price': 'Prints from $40',
                                              'performers': [], 'links': [{'title': 'x', 'url': 'http://insecure.example'}]}},
                    {'ref': 's9', 'context': {'about': 'Not asked for'}}],
          'places': [{'ref': 'l0', 'context': {'hours': '5–8 PM'}}]}


class DetailFillTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user('o', 'o@example.com', 'pw')
        start = timezone.now() + timedelta(days=2)
        self.event = Event.objects.create(name='Third Thursday', event_code='TT2VLQ', user=self.owner, start_time=start,
                                          timezone='America/Chicago', source_urls=['https://fest.example/'])
        self.place = Location.objects.create(event=self.event, name='The Nobleman Hotel')
        self.item = ScheduledItem.objects.create(event=self.event, name='Allyn Fincher', start_time=start,
                                                 location=self.place)
        self.done = ScheduledItem.objects.create(event=self.event, name='Amelia Rose', start_time=start,
                                                 context={'about': 'Written by hand'}, locked_fields=['context'])

    def test_excerpts_only_mention_the_entity(self):
        content = 'x' * 5000 + ' Allyn Fincher paints skies. ' + 'y' * 5000
        text = detail_fill.excerpts('Allyn Fincher', content)
        self.assertIn('Allyn Fincher paints skies', text)
        self.assertLess(len(text), 2 * detail_fill.WINDOW + 100)

    def test_fill_only_empty_contexts_normalised(self):
        data = {'schedule': [{'name': 'Allyn Fincher', 'context': {}}, {'name': 'Amelia Rose', 'context': {'about': 'x'}}],
                'locations': [{'name': 'The Nobleman Hotel', 'context': {}}]}
        with mock.patch('attnd.claude.available', return_value=True), \
                mock.patch('attnd.claude.structured', return_value=(ANSWER, 0.01)) as ask:
            done = detail_fill.fill(data, 'Allyn Fincher at The Nobleman Hotel, 5–8 PM. Prints from $40.')
        self.assertEqual(done, {'items': 1, 'places': 1})
        self.assertEqual(data['schedule'][0]['context']['price'], 'Prints from $40')
        self.assertNotIn('links', data['schedule'][0]['context'])  # http links dropped by normalize
        self.assertEqual(data['schedule'][1]['context'], {'about': 'x'})  # never replaced
        self.assertEqual(ask.call_count, 1)
        self.assertIn('### s0 (item) Allyn Fincher', ask.call_args.kwargs['content'][0]['text'])
        self.assertEqual(detail_fill.done_text(done), 'Added details for 1 schedule item and 1 place')

    def test_fill_in_details_job_keeps_hand_written_ones(self):
        client = Client()
        client.force_login(self.owner)
        with self.captureOnCommitCallbacks(execute=False):
            response = client.post(reverse('console:event_fill_details', args=[self.event.pk]),
                                   HTTP_ACCEPT='application/json', secure=True)
        self.assertIn(response.status_code, (202, 302))
        job = IngestJob.objects.get(kind=IngestJob.KIND_DETAILS)
        from scrape import jobs
        with mock.patch('scrape.service.gather_sources', return_value='Allyn Fincher paints. The Nobleman Hotel 5–8 PM'), \
                mock.patch('attnd.claude.available', return_value=True), \
                mock.patch('attnd.claude.structured', return_value=(ANSWER, 0.01)):
            jobs.run_job(job.pk)
        job.refresh_from_db()
        self.assertEqual(job.status, IngestJob.DONE, job.error)
        self.item.refresh_from_db()
        self.done.refresh_from_db()
        self.place.refresh_from_db()
        self.assertEqual(self.item.context['about'], 'Painter of Texas skies.')
        self.assertEqual(self.done.context, {'about': 'Written by hand'})
        self.assertEqual(self.place.context['hours'], '5–8 PM')
        self.assertEqual(job.result['details_items'], 1)

    def test_readiness_offers_fill_in_details(self):
        from console.components.catalog_chat import checks
        rows = {c['key']: c for c in checks(self.event)}
        self.assertIn('Details on 1 of 2 schedule items', rows['details']['text'])
        self.assertEqual(rows['details']['link'], 'Fill in details')
        self.assertTrue(rows['details']['post'])
