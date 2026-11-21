"""Stage I3: paste or drop anything into an event (scrape.tables, console:event_import, the paste job)."""
import io
import zipfile
from datetime import timedelta
from unittest import mock

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from events.models import Event, Location
from scrape import jobs
from scrape.models import ChangeSet, IngestJob
from scrape.tables import XLSX, to_text


def xlsx(rows):
    out = io.BytesIO()
    strings = sorted({c for r in rows for c in r})
    with zipfile.ZipFile(out, 'w') as z:
        z.writestr('xl/workbook.xml', '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                   '<sheets><sheet name="Lineup" sheetId="1"/></sheets></workbook>')
        z.writestr('xl/sharedStrings.xml', '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                   + ''.join(f'<si><t>{s}</t></si>' for s in strings) + '</sst>')
        cells = ''.join('<row r="%d">%s</row>' % (i + 1, ''.join(
            f'<c r="{chr(65 + j)}{i + 1}" t="s"><v>{strings.index(v)}</v></c>' for j, v in enumerate(r)))
            for i, r in enumerate(rows))
        z.writestr('xl/worksheets/sheet1.xml', '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/'
                   f'2006/main"><sheetData>{cells}</sheetData></worksheet>')
    return out.getvalue()


class TableTests(TestCase):
    def test_csv_and_xlsx_become_tab_separated_text(self):
        self.assertEqual(to_text('a.csv', 'text/csv', b'Time,Act\n7 PM,"Prairie Lights, live"\n'),
                         'Time\tAct\n7 PM\tPrairie Lights, live')
        text = to_text('lineup.xlsx', XLSX, xlsx([['Time', 'Act'], ['7 PM', 'Prairie Lights']]))
        self.assertIn('Sheet: Lineup', text)
        self.assertIn('7 PM\tPrairie Lights', text)
        self.assertIsNone(to_text('flyer.png', 'image/png', b'\x89PNG'))


class PasteImportTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user('o', 'o@example.com', 'pw')
        self.event = Event.objects.create(name='Fest', event_code='FESTP', user=self.owner, timezone='America/Chicago',
                                          start_time=timezone.now() + timedelta(days=3),
                                          source_urls=['https://fest.example/'])
        self.keep = Location.objects.create(event=self.event, name='Main Stage')
        self.client.force_login(self.owner)

    def test_a_spreadsheet_becomes_a_review_without_crawling_or_removals(self):
        upload = SimpleUploadedFile('lineup.xlsx', xlsx([['Place', 'Kind'], ['Taco Row', 'Food']]), content_type=XLSX)
        with mock.patch('scrape.jobs.enqueue'):
            response = self.client.post(reverse('console:event_import', args=[self.event.pk]),
                                        {'upload': upload, 'text': 'Also a lemonade stand.'},
                                        HTTP_ACCEPT='application/json', secure=True)
        self.assertEqual(response.status_code, 202)
        job = IngestJob.objects.get()
        self.assertEqual(job.trigger, IngestJob.TRIGGER_PASTE)
        self.assertIn('Taco Row\tFood', job.text)
        self.assertIsNone(job.upload_data)
        extracted = {'event': {'name': 'Fest'}, 'locations': [{'name': 'Taco Row', 'category': 'Food'}],
                     'schedule': [], 'notes': ''}
        with mock.patch('scrape.jobs.scrape_event', return_value=extracted) as scrape:
            jobs.run_job(job.pk)
        self.assertEqual(scrape.call_args.kwargs['urls'], [])  # never the website
        self.assertFalse(scrape.call_args.kwargs['crawl'])
        cs = ChangeSet.objects.get(event=self.event)
        ops = set(cs.changes.values_list('op', flat=True))
        self.assertNotIn('delete', ops)  # Main Stage isn't in the sheet, and stays
        self.assertTrue(Location.objects.filter(pk=self.keep.pk).exists())
        self.assertFalse(Location.objects.filter(name='Taco Row').exists())  # waits for review

    def test_nothing_to_read_is_refused(self):
        response = self.client.post(reverse('console:event_import', args=[self.event.pk]), {},
                                    HTTP_ACCEPT='application/json', secure=True)
        self.assertEqual(response.status_code, 400)
