"""Diagnostics (API-013) and anonymous performance counts (API-014)."""
import gzip
import json
import re
from datetime import timedelta
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

from django.conf import settings
from django.contrib.auth.models import User
from django.core.cache import cache
from django.core.management import call_command
from django.test import Client, RequestFactory, SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from console.testing import TwoFactorClient
from diagnostics import records, retrace, storage, symbolicate, timing
from diagnostics.middleware import path_template
from diagnostics.models import (APP_CATEGORIES, LEVELS, DiagnosticEntry, DiagnosticReport, DiagnosticSession,
                                EndpointTiming, SymbolFile)
from diagnostics.purge import purge
from events.models import Event, UsageCount

KEY = 'diag-test-key'
CI_KEY = 'ci-key-for-diagnostics-tests-0123456789abc'
AUTH = {'HTTP_AUTHORIZATION': f'Bearer {KEY}'}
CODE = 'D-7K3QX'


def record(**overrides):
    entry = {'v': 1, 'ts': timezone.now().isoformat(), 'mono_ms': 523101, 'seq': 1, 'level': 'debug', 'cat': 'net',
             'event': 'net.request.finished', 'msg': 'GET bundle 304 in 182 ms',
             'f': {'method': 'GET', 'path': '/api/v2/events/{id}/bundle/', 'status': 304, 'ms': 182, 'req_id': 'abcd1234ef'},
             'app': '2.0.131', 'build': '131', 'os': 'iOS 27.2', 'model': 'iPhone18,1', 'state': 'fg', 'net': 'wifi',
             'event_id': 5, 'diag': CODE}
    entry.update(overrides)
    return entry


def batch(entries=None, **overrides):
    body = {'session': CODE, 'platform': 'ios', 'app': '2.0.131', 'build': '131', 'os': 'iOS 27.2',
            'model': 'iPhone18,1', 'reason': 'debug_mode', 'entries': entries if entries is not None else [record()]}
    body.update(overrides)
    return body


@override_settings(API_KEY=KEY, CI_API_KEYS=[CI_KEY])
class UploadTestCase(TestCase):
    def setUp(self):
        cache.clear()
        self.client = Client()

    def post(self, path, body, *, gz=False, raw=None, key=KEY, **extra):
        data = raw if raw is not None else json.dumps(body).encode()
        if gz:
            data = gzip.compress(data)
            extra['HTTP_CONTENT_ENCODING'] = 'gzip'
        return self.client.post(path, data, content_type='application/json', secure=True,
                                HTTP_AUTHORIZATION=f'Bearer {key}', **extra)

    def logs(self, body, **kw):
        return self.post('/api/v2/diagnostics/logs/', body, **kw)


class LogUploadTests(UploadTestCase):
    def test_accepts_a_gzip_batch_and_stores_the_session(self):
        resp = self.logs(batch([record(), record(seq=2, level='error', cat='ui', event='error.caught', msg='boom',
                                               f={'domain': 'NSURLErrorDomain', 'code': -1001})]), gz=True)
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.json(), {'status': 'ok', 'accepted': 2, 'rejected': 0})
        session = DiagnosticSession.objects.get(code=CODE)
        self.assertEqual((session.platform, session.reason, session.app_version, session.build, session.model),
                         ('ios', 'debug_mode', '2.0.131', '131', 'iPhone18,1'))
        self.assertEqual((session.entry_count, session.error_count, session.event_id), (2, 1, 5))
        entry = session.entries.get(seq=1)
        self.assertEqual((entry.level, entry.cat, entry.name, entry.req_id, entry.state, entry.net),
                         (LEVELS.index('debug'), 'net', 'net.request.finished', 'abcd1234ef', 'fg', 'wifi'))

    def test_plain_json_and_lower_case_code(self):
        resp = self.logs(batch(session='d-7k3qx', entries=[record(diag='d-7k3qx')]))
        self.assertEqual(resp.json()['accepted'], 1)
        self.assertTrue(DiagnosticSession.objects.filter(code=CODE).exists())

    def test_invalid_records_are_dropped_one_by_one(self):
        old = (timezone.now() - timedelta(days=30)).isoformat()
        bad = [record(v=2), record(level='loud'), record(cat='server'), record(cat='nope'), record(event='Bad Name'),
               record(event='single'), record(ts='yesterday'), record(ts=old), record(ts='2026-11-15T10:00:00'),
               record(diag='D-AAAAA'), 'not a dict']
        resp = self.logs(batch([record(), record(level=4), *bad]))
        self.assertEqual(resp.json(), {'status': 'ok', 'accepted': 2, 'rejected': len(bad)})

    def test_every_level_and_category(self):
        entries = [record(level=lv, seq=i) for i, lv in enumerate(LEVELS)] + [record(cat=c) for c in APP_CATEGORIES]
        self.assertEqual(self.logs(batch(entries)).json()['accepted'], len(entries))

    def test_request_errors(self):
        self.assertEqual(self.logs(batch(session='D-12')).status_code, 400)
        self.assertEqual(self.logs(batch(session='D-ILOU1')).status_code, 400)  # not Crockford base32
        self.assertEqual(self.logs(batch(platform='web')).status_code, 400)
        self.assertEqual(self.logs(batch(reason='always')).status_code, 400)
        self.assertEqual(self.logs(batch(entries='x')).status_code, 400)
        self.assertEqual(self.logs(None, raw=b'not json').status_code, 400)
        self.assertEqual(self.logs(None, raw=b'\x1f\x8bnot gzip').status_code, 400)
        self.assertEqual(self.logs(batch([record()] * 501)).status_code, 413)
        self.assertEqual(self.logs(None, raw=b'{"x": "' + b'a' * (300 * 1024) + b'"}').status_code, 413)
        bomb = json.dumps(batch([record(msg='a' * 1900)] * 400)).encode() + b' ' * (3 * 1024 * 1024)
        self.assertEqual(self.logs(None, raw=bomb, gz=True).status_code, 413)  # > 2 MB once decompressed
        self.assertEqual(self.client.post('/api/v2/diagnostics/logs/', '{}', content_type='application/json',
                                          secure=True).status_code, 401)
        self.assertFalse(DiagnosticEntry.objects.exists())

    def test_ci_key_gets_the_same_answer_and_nothing_is_stored(self):
        resp = self.logs(batch([record(), record(v=9)]), key=CI_KEY)
        self.assertEqual(resp.json(), {'status': 'ok', 'accepted': 1, 'rejected': 1})
        self.assertFalse(DiagnosticSession.objects.exists())

    def test_server_side_redaction(self):
        fields = {'path': '/api/v2/events/{id}/lookup/?q=secret', 'url': 'https://x/y?token=1#frag',
                  'push_token': 'abc', 'apnsToken': 'abc', 'authorization': 'Bearer x', 'email': 'a@b.c',
                  'device_id': 'u-1', 'query': 'tacos near me', 'query_len': 13, 'lat': 32.75123, 'lng': -97.3333,
                  'nested': {'api_key': 'k', 'ok': 1}, 'context': 'kept'}
        self.logs(batch([record(f=fields)]))
        stored = DiagnosticEntry.objects.get().fields
        self.assertEqual(stored, {'path': '/api/v2/events/{id}/lookup/', 'url': 'https://x/y', 'query_len': 13,
                                  'lat': 32.75, 'lng': -97.33, 'nested': {'ok': 1}, 'context': 'kept'})

    def test_dev_builds_may_log_typed_text(self):
        self.logs(batch([record(f={'query': 'tacos', 'token': 'x'})], reason='dev'))
        self.assertEqual(DiagnosticEntry.objects.get().fields, {'query': 'tacos'})

    def test_oversized_fields_and_message_are_cut(self):
        self.logs(batch([record(msg='m' * 5000, f={f'k{i}': 'v' * 400 for i in range(40)})]))
        entry = DiagnosticEntry.objects.get()
        self.assertEqual(len(entry.msg), records.MAX_MSG)
        self.assertEqual(entry.fields, {'_truncated': True})

    def test_appends_to_an_existing_session(self):
        self.logs(batch([record(seq=1)]))
        self.logs(batch([record(seq=2, event_id=None)], app='2.0.132'))
        session = DiagnosticSession.objects.get()
        self.assertEqual((session.entry_count, session.app_version, session.event_id), (2, '2.0.132', 5))


class CapTests(UploadTestCase):
    @override_settings(DIAGNOSTICS_SESSION_MAX_ENTRIES=3)
    def test_per_session_window_answers_429_with_retry_after(self):
        self.assertEqual(self.logs(batch([record()] * 3)).status_code, 200)
        resp = self.logs(batch([record()]))
        self.assertEqual(resp.status_code, 429)
        retry = int(resp['Retry-After'])
        self.assertTrue(1 <= retry <= 601, retry)
        self.assertEqual(resp.json()['retry_after'], retry)
        # the window moves on after 10 minutes
        DiagnosticSession.objects.update(window_start=timezone.now() - timedelta(seconds=601))
        self.assertEqual(self.logs(batch([record()])).status_code, 200)
        self.assertEqual(DiagnosticEntry.objects.count(), 4)

    def test_full_storage_answers_429_for_an_hour(self):
        with patch('diagnostics.storage.used_bytes', return_value=settings.DIAGNOSTICS_MAX_BYTES):
            resp = self.logs(batch())
            self.assertEqual((resp.status_code, resp['Retry-After']), (429, '3600'))
            reports = self.post('/api/v2/diagnostics/reports/', {'platform': 'ios', 'reports': [{'kind': 'crash'}]})
            self.assertEqual(reports.status_code, 429)
        self.assertFalse(DiagnosticEntry.objects.exists())

    def test_size_check_is_cheap_and_cached(self):
        self.logs(batch())
        first = storage.used_bytes(fresh=True)
        self.assertGreater(first, 0)
        with patch('diagnostics.storage.measure') as measure:
            self.assertEqual(storage.used_bytes(), first)
            measure.assert_not_called()

    @override_settings(RATE_LIMITS={**settings.RATE_LIMITS, 'diagnostics_ip': (2, 600)})
    def test_uploads_are_limited_per_ip(self):
        self.assertEqual(self.logs(batch()).status_code, 200)
        self.assertEqual(self.logs(batch()).status_code, 200)
        resp = self.logs(batch())
        self.assertEqual((resp.status_code, resp['Retry-After']), (429, '600'))


class ReportUploadTests(UploadTestCase):
    def report_body(self, **overrides):
        report = {'kind': 'crash', 'occurred_at': timezone.now().isoformat(),
                  'exception': {'type': 'java.lang.IllegalStateException', 'message': 'boom\nsecond line'},
                  'stack': 'java.lang.IllegalStateException: boom\n    at a.a.b(SourceFile:3)\n'}
        report.update(overrides)
        return {'platform': 'android', 'app': '2.0.131', 'build': '131000', 'os': 'Android 16', 'model': 'Pixel 9',
                'reports': [report]}

    def test_accepts_reports_without_any_device_id(self):
        resp = self.post('/api/v2/diagnostics/reports/', self.report_body(device_id='u-1'), gz=True)
        self.assertEqual(resp.json(), {'status': 'ok', 'accepted': 1, 'rejected': 0})
        report = DiagnosticReport.objects.get()
        self.assertEqual((report.platform, report.kind, report.build, report.model, report.summary),
                         ('android', 'crash', '131000', 'Pixel 9', 'java.lang.IllegalStateException: boom'))
        self.assertNotIn('u-1', json.dumps([report.summary, report.stack, report.payload]))

    def test_same_problem_same_signature(self):
        self.post('/api/v2/diagnostics/reports/', self.report_body())
        self.post('/api/v2/diagnostics/reports/', self.report_body(occurred_at=None))
        self.post('/api/v2/diagnostics/reports/', self.report_body(stack='x\n    at a.a.c(SourceFile:9)'))
        signatures = list(DiagnosticReport.objects.order_by('pk').values_list('signature', flat=True))
        self.assertEqual(signatures[0], signatures[1])
        self.assertNotEqual(signatures[0], signatures[2])

    def test_invalid_reports(self):
        body = self.report_body()
        body['reports'] += [{'kind': 'explosion'}, 'x', {'kind': 'hang', 'payload': {'x': 'y' * 300_000}}]
        self.assertEqual(self.post('/api/v2/diagnostics/reports/', body, gz=True).json()['rejected'], 3)
        self.assertEqual(self.post('/api/v2/diagnostics/reports/', {'platform': 'x', 'reports': []}).status_code, 400)
        self.assertEqual(self.post('/api/v2/diagnostics/reports/',
                                   {'platform': 'ios', 'reports': [{'kind': 'crash'}] * 21}).status_code, 413)

    def test_ci_key_discards_reports(self):
        resp = self.post('/api/v2/diagnostics/reports/', self.report_body(), key=CI_KEY)
        self.assertEqual(resp.json()['accepted'], 1)
        self.assertFalse(DiagnosticReport.objects.exists())


# ---------------------------------------------------------------------------
# Retention and the size cap
# ---------------------------------------------------------------------------

def make_session(code, reason, received_days_ago, entries=2, size=1000):
    received = timezone.now() - timedelta(days=received_days_ago)
    session = DiagnosticSession.objects.create(code=code, reason=reason, platform='ios', last_entry_at=received,
                                               entry_count=entries, bytes=size)
    DiagnosticEntry.objects.bulk_create([
        DiagnosticEntry(session=session, received_at=received, ts=received, level=2, cat='ui', name='ui.screen')
        for _ in range(entries)])
    return session


class PurgeTests(TestCase):
    def test_retention_dev_7_days_debug_14_days(self):
        make_session('D-AAAA1', 'dev', 8)
        make_session('D-AAAA2', 'dev', 6)
        make_session('D-AAAA3', 'debug_mode', 13)
        make_session('D-AAAA4', 'debug_mode', 15)
        result = purge()
        self.assertEqual(sorted(DiagnosticSession.objects.values_list('code', flat=True)), ['D-AAAA2', 'D-AAAA3'])
        self.assertEqual((result['entries'], result['sessions']), (4, 2))

    def test_a_dev_install_keeps_its_session_but_loses_old_entries(self):
        session = make_session('D-AAAA1', 'dev', 9)
        now = timezone.now()
        DiagnosticEntry.objects.create(session=session, received_at=now, ts=now, level=2, cat='ui', name='ui.screen')
        DiagnosticSession.objects.filter(pk=session.pk).update(last_entry_at=now)
        purge()
        self.assertEqual(session.entries.count(), 1)

    def test_reports_timings_and_symbol_files(self):
        old = DiagnosticReport.objects.create(platform='ios', kind='crash', signature='s')
        DiagnosticReport.objects.filter(pk=old.pk).update(received_at=timezone.now() - timedelta(days=31))
        DiagnosticReport.objects.create(platform='ios', kind='crash', signature='s')
        EndpointTiming.objects.create(hour=timezone.now() - timedelta(days=31), method='GET', route='/x')
        for i in range(3):
            SymbolFile.objects.create(app_version=f'2.0.{i}', build=str(i), data=gzip.compress(b'x'), size=1, sha256='0')
        with override_settings(DIAGNOSTICS_MAX_SYMBOL_FILES=2):
            result = purge()
        self.assertEqual((result['reports'], result['timings'], result['symbols']), (1, 1, 1))
        self.assertEqual(DiagnosticReport.objects.count(), 1)
        self.assertEqual(SymbolFile.objects.count(), 2)

    def test_size_cap_deletes_the_oldest_sessions_first(self):
        for i, days in enumerate((5, 4, 3, 2, 1)):
            make_session(f'D-BBBB{i}', 'debug_mode', days, size=100)
        cap = settings.DIAGNOSTICS_MAX_BYTES
        # 500 recorded bytes take 110 % of the cap on disk: each session ≈ 22 %; to get under 80 %: delete 2.
        with patch('diagnostics.storage.used_bytes', return_value=int(cap * 1.1)):
            result = purge()
        self.assertEqual(result['capped_sessions'], 2)
        self.assertEqual(sorted(DiagnosticSession.objects.values_list('code', flat=True)),
                         ['D-BBBB2', 'D-BBBB3', 'D-BBBB4'])
        self.assertFalse(result['vacuumed'])  # never inside a transaction

    def test_under_the_cap_nothing_else_is_deleted(self):
        make_session('D-CCCC1', 'debug_mode', 1)
        self.assertEqual(purge()['capped_sessions'], 0)
        self.assertTrue(DiagnosticSession.objects.exists())

    def test_run_scheduled_tasks_purges(self):
        make_session('D-AAAA4', 'debug_mode', 15)
        with patch('events.push.send_due_notifications', return_value=0), \
                patch('scrape.watch.check_watched_events', return_value=(0, 0)), \
                patch('events.management.commands.run_scheduled_tasks.keep_demo_current'):
            call_command('run_scheduled_tasks', stdout=StringIO())
        self.assertFalse(DiagnosticSession.objects.exists())

    def test_purge_command(self):
        out = StringIO()
        call_command('purge_diagnostics', stdout=out)
        self.assertIn('diagnostics purge', out.getvalue())


# ---------------------------------------------------------------------------
# R8 retrace
# ---------------------------------------------------------------------------

MAPPING = """\
# compiler: R8
# {"id":"com.android.tools.r8.mapping","version":"2.2"}
com.arcu.attnd.ui.EventViewModel -> a.b.c:
# {"id":"sourceFile","fileName":"EventViewModel.kt"}
    kotlinx.coroutines.flow.StateFlow state -> a
    1:1:void <init>():12:12 -> <init>
    1:4:void refresh(boolean):40:43 -> b
    5:5:void com.arcu.attnd.data.EventRepository.load(int):88:88 -> b
    5:5:void refresh(boolean):44 -> b
    6:9:void track(java.lang.String):100 -> b
    void unused() -> c
    void other(int) -> c
    1:3:void keepLines() -> d
com.arcu.attnd.data.EventRepository -> a.b.d:
# {"id":"sourceFile","fileName":"EventRepository.kt"}
    1:2:void load(int):80:81 -> a
com.arcu.attnd.data.ApiException -> a.b.e:
    void <init>(java.lang.String) -> <init>
com.arcu.attnd.Unused -> a.z:
"""

STACK = """\
a.b.e: bundle failed
\tat a.b.c.b(SourceFile:3)
\tat a.b.c.b(SourceFile:5)
\tat a.b.c.b(SourceFile:7)
\tat a.b.c.c(Unknown Source)
\tat a.b.c.d(SourceFile:2)
\tat a.b.d.a(SourceFile:2)
\tat android.os.Handler.dispatchMessage(Handler.java:99)
Caused by: a.b.e: inner
\t... 5 more"""


class RetraceTests(SimpleTestCase):
    def lines(self):
        return retrace.retrace(STACK, MAPPING).splitlines()

    def test_exception_class_names(self):
        out = self.lines()
        self.assertEqual(out[0], 'com.arcu.attnd.data.ApiException: bundle failed')
        self.assertIn('Caused by: com.arcu.attnd.data.ApiException: inner', out)
        self.assertEqual(out[-1], '\t... 5 more')

    def test_line_by_line_range(self):
        self.assertEqual(self.lines()[1], '\tat com.arcu.attnd.ui.EventViewModel.refresh(EventViewModel.kt:42)')

    def test_inlined_chain_innermost_first(self):
        out = self.lines()
        self.assertEqual(out[2], '\tat com.arcu.attnd.data.EventRepository.load(EventRepository.kt:88)')
        self.assertEqual(out[3], '\tat com.arcu.attnd.ui.EventViewModel.refresh(EventViewModel.kt:44)')

    def test_single_original_line_for_a_range(self):
        self.assertEqual(self.lines()[4], '\tat com.arcu.attnd.ui.EventViewModel.track(EventViewModel.kt:100)')

    def test_ambiguous_without_line_lists_alternatives(self):
        out = self.lines()
        self.assertEqual(out[5], '\tat com.arcu.attnd.ui.EventViewModel.unused(EventViewModel.kt)')
        self.assertEqual(out[6], '\t<OR> at com.arcu.attnd.ui.EventViewModel.other(EventViewModel.kt)')

    def test_range_without_original_lines_keeps_the_line(self):
        self.assertEqual(self.lines()[7], '\tat com.arcu.attnd.ui.EventViewModel.keepLines(EventViewModel.kt:2)')

    def test_other_class_and_unobfuscated_frames(self):
        out = self.lines()
        self.assertEqual(out[8], '\tat com.arcu.attnd.data.EventRepository.load(EventRepository.kt:81)')
        self.assertEqual(out[9], '\tat android.os.Handler.dispatchMessage(Handler.java:99)')

    def test_mapping_keeps_only_wanted_classes(self):
        mapping = retrace.Mapping(MAPPING, wanted={'a.b.d'})
        self.assertEqual(list(mapping.by_obfuscated), ['a.b.d'])
        self.assertEqual(mapping.original_names['a.z'], 'com.arcu.attnd.Unused')

    def test_unknown_method_keeps_the_frame_with_the_class_name(self):
        out = retrace.retrace('\tat a.b.c.zz(SourceFile:1)', MAPPING)
        self.assertEqual(out, '\tat com.arcu.attnd.ui.EventViewModel.zz(SourceFile:1)')


# ---------------------------------------------------------------------------
# Request log middleware and endpoint timings
# ---------------------------------------------------------------------------

@override_settings(API_KEY=KEY, CI_API_KEYS=[CI_KEY])
class RequestLogTests(TestCase):
    def setUp(self):
        cache.clear()
        timing.reset()
        self.event = Event.objects.create(name='Fair', event_code='FAIR', start_time=timezone.now(), timezone='UTC')

    def get(self, path, key=KEY, **headers):
        return self.client.get(path, secure=True, HTTP_AUTHORIZATION=f'Bearer {key}', **headers)

    def test_request_id_is_echoed_or_made(self):
        resp = self.get('/api/v2/events/lookup/?q=FAIR', HTTP_X_REQUEST_ID='app-req-12345678')
        self.assertEqual(resp['X-Request-Id'], 'app-req-12345678')
        made = self.get('/api/v2/events/lookup/?q=FAIR', HTTP_X_REQUEST_ID='bad id <script>')['X-Request-Id']
        self.assertRegex(made, r'^[0-9a-f]{16}$')
        self.assertNotIn('X-Request-Id', self.client.get('/', secure=True))

    def test_one_json_line_with_the_path_template_never_the_query(self):
        with self.assertLogs('attnd.request', 'INFO') as logs:
            self.get(f'/api/v2/events/{self.event.id}/bundle/?secret=1', HTTP_X_REQUEST_ID='req-abcdefgh',
                     HTTP_X_DIAG_SESSION='d-7k3qx')
        line = json.loads(logs.records[0].getMessage())
        self.assertEqual(line['path'], '/api/v2/events/{id}/bundle/')
        self.assertEqual((line['method'], line['status'], line['req_id'], line['diag']),
                         ('GET', 200, 'req-abcdefgh', CODE))
        self.assertIsInstance(line['ms'], int)
        self.assertNotIn('secret', logs.output[0])
        self.assertNotIn(str(self.event.id) + '/', line['path'])

    def test_debug_session_gets_the_server_line(self):
        self.get(f'/api/v2/events/{self.event.id}/bundle/', HTTP_X_REQUEST_ID='req-abcdefgh',
                 HTTP_X_DIAG_SESSION=CODE)
        entry = DiagnosticEntry.objects.get()
        self.assertEqual((entry.source, entry.cat, entry.name, entry.req_id, entry.session.code),
                         ('server', 'server', 'server.request', 'req-abcdefgh', CODE))
        self.assertEqual(entry.fields['status'], 200)
        self.assertEqual(entry.fields['path'], '/api/v2/events/{id}/bundle/')
        # a 404 is a warning line
        self.get('/api/v2/events/999999/bundle/', HTTP_X_DIAG_SESSION=CODE)
        self.assertEqual(DiagnosticEntry.objects.order_by('-id').first().level, LEVELS.index('warning'))

    def test_no_server_line_without_auth_for_ci_keys_or_for_uploads(self):
        self.client.get('/api/v2/events/lookup/?q=FAIR', secure=True, HTTP_X_DIAG_SESSION=CODE)  # 401
        self.get('/api/v2/events/lookup/?q=FAIR', key=CI_KEY, HTTP_X_DIAG_SESSION=CODE)
        self.client.post('/api/v2/diagnostics/logs/', json.dumps(batch()), content_type='application/json',
                         secure=True, HTTP_X_DIAG_SESSION=CODE, **AUTH)
        self.assertFalse(DiagnosticEntry.objects.filter(source='server').exists())

    def test_path_templates(self):
        factory = RequestFactory()
        from django.urls import resolve
        cases = {
            f'/api/v2/events/{self.event.id}/join/': '/api/v2/events/{id}/join/',
            '/api/v1/events/nearby_events/32.7/-97.3/': '/api/v1/events/nearby_events/{latitude}/{longitude}/',
            '/api/v7/events/': '/api/v{version}/*',
            '/api/diagnostics/symbolicate/5/': '/api/diagnostics/symbolicate/{id}/',
        }
        for url, expected in cases.items():
            request = factory.get(url)
            request.resolver_match = resolve(url)
            self.assertEqual(path_template(request), expected, url)
        self.assertEqual(path_template(factory.get('/api/nothing')), 'unmatched')

    @override_settings(DIAGNOSTICS_TIMING=True)
    def test_timings_and_percentiles(self):
        for _ in range(3):
            self.get('/api/v2/events/lookup/?q=FAIR')
        self.get('/api/v2/events/lookup/?q=NOPE')
        timing.flush()
        row = EndpointTiming.objects.get(route='/api/v2/events/lookup/')
        self.assertEqual((row.method, row.count, row.client_errors, row.server_errors), ('GET', 4, 1, 0))
        self.assertEqual(sum(row.hist), 4)
        table = timing.table(timezone.now() - timedelta(hours=1))
        self.assertEqual(table[0]['count'], 4)
        hist = [0] * (len(timing.BUCKETS_MS) + 1)
        hist[timing.BUCKETS_MS.index(100)] = 10  # all between 75 and 100 ms
        self.assertEqual(timing.percentile(hist, .5), 88)
        self.assertEqual(timing.percentile([0] * len(hist), .5), None)


# ---------------------------------------------------------------------------
# Symbols (CI) and iOS symbolication
# ---------------------------------------------------------------------------

@override_settings(API_KEY=KEY, CI_API_KEYS=[CI_KEY], STATIC_API_KEY_ENABLED=False)
class SymbolUploadTests(UploadTestCase):
    url = '/api/diagnostics/symbols/?platform=android&version=2.0.131&build=131000'

    def upload(self, data, key=CI_KEY, url=None):
        return self.client.post(url or self.url, data, content_type='application/octet-stream', secure=True,
                                HTTP_AUTHORIZATION=f'Bearer {key}')

    def test_ci_key_uploads_gzip_or_plain_and_replaces_the_build(self):
        resp = self.upload(gzip.compress(MAPPING.encode()))
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.json()['bytes'], len(MAPPING.encode()))
        self.assertEqual(self.upload(MAPPING.encode() + b'\n').status_code, 200)
        row = SymbolFile.objects.get()
        self.assertEqual((row.app_version, row.build), ('2.0.131', '131000'))
        self.assertEqual(row.text(), MAPPING + '\n')

    def test_refused(self):
        self.assertEqual(self.upload(MAPPING.encode(), key=KEY).status_code, 401)  # static key is off here
        self.assertEqual(self.upload(MAPPING.encode(), key='nope').status_code, 401)
        self.assertEqual(self.upload(b'hello world').status_code, 400)
        self.assertEqual(self.upload(MAPPING.encode(), url='/api/diagnostics/symbols/?version=1').status_code, 400)
        self.assertEqual(self.upload(MAPPING.encode(), url=self.url + '&platform=ios').status_code, 400)
        self.assertFalse(SymbolFile.objects.exists())

    @override_settings(STATIC_API_KEY_ENABLED=True)
    def test_static_key_where_it_is_on(self):
        self.assertEqual(self.upload(MAPPING.encode(), key=KEY).status_code, 200)


PAYLOAD = {
    'diagnosticMetaData': {'appBuildVersion': '131', 'exceptionType': 1, 'signal': 11},
    'callStackTree': {'callStackPerThread': True, 'callStacks': [
        {'threadAttributed': False, 'callStackRootFrames': [
            {'binaryUUID': 'bbbb', 'binaryName': 'libsystem_kernel.dylib', 'offsetIntoBinaryTextSegment': 10}]},
        {'threadAttributed': True, 'callStackRootFrames': [
            {'binaryUUID': 'aaaa-1', 'binaryName': 'Attnd', 'offsetIntoBinaryTextSegment': 4096, 'subFrames': [
                {'binaryUUID': 'aaaa-1', 'binaryName': 'Attnd', 'offsetIntoBinaryTextSegment': 8192}]}]},
    ]},
}


@override_settings(GITHUB_SYMBOLICATE_TOKEN='gh-token', GITHUB_SYMBOLICATE_REPO='apbeers/Attnd-iOS',
                   GITHUB_SYMBOLICATE_REF='main')
class SymbolicateTests(TestCase):
    def setUp(self):
        self.report = DiagnosticReport.objects.create(platform='ios', kind='crash', build='131', app_version='2.0.131',
                                                      signature='s', payload=PAYLOAD)

    def test_frames_attributed_thread_first(self):
        self.assertEqual(symbolicate.frames_of(PAYLOAD), [
            {'uuid': 'AAAA-1', 'binary': 'Attnd', 'offset': 4096}, {'uuid': 'AAAA-1', 'binary': 'Attnd', 'offset': 8192},
            {'uuid': 'BBBB', 'binary': 'libsystem_kernel.dylib', 'offset': 10}])

    def dispatch(self):
        response = MagicMock(status=204)
        response.__enter__.return_value = response
        with patch('urllib.request.urlopen', return_value=response) as urlopen:
            ok, message = symbolicate.request(self.report, site_url='https://dev.attnd.app')
        self.assertTrue(ok, message)
        req = urlopen.call_args[0][0]
        body = json.loads(req.data)
        return req, body

    def test_dispatch_then_callback(self):
        req, body = self.dispatch()
        self.assertEqual(req.full_url,
                         'https://api.github.com/repos/apbeers/Attnd-iOS/actions/workflows/symbolicate.yml/dispatches')
        self.assertEqual(req.headers['Authorization'], 'Bearer gh-token')
        self.assertEqual(body['ref'], 'main')
        token = body['inputs'].pop('token')
        self.assertEqual(body['inputs'], {'report_id': str(self.report.pk), 'build': '131', 'version': '2.0.131',
                                          'server': 'https://dev.attnd.app'})
        url = f'/api/diagnostics/symbolicate/{self.report.pk}/'
        self.assertEqual(self.client.get(url, secure=True, HTTP_AUTHORIZATION='Bearer wrong').status_code, 401)
        job = self.client.get(url, secure=True, HTTP_AUTHORIZATION=f'Bearer {token}').json()
        self.assertEqual((job['build'], len(job['frames'])), ('131', 3))
        resp = self.client.post(url, json.dumps({'frames': [
            {'uuid': 'aaaa-1', 'offset': 4096, 'symbol': 'EventViewModel.refresh() (EventViewModel.swift:42)'},
            {'uuid': 'AAAA-1', 'offset': 8192, 'symbol': 'closure in AttndApp.body'}]}),
            content_type='application/json', secure=True, HTTP_AUTHORIZATION=f'Bearer {token}')
        self.assertEqual(resp.json(), {'status': 'ok', 'symbols': 2})
        self.report.refresh_from_db()
        self.assertEqual(self.report.symbolicate_status, 'done')
        self.assertIn('EventViewModel.refresh() (EventViewModel.swift:42)', symbolicate.render(self.report))
        # single use
        self.assertEqual(self.client.get(url, secure=True, HTTP_AUTHORIZATION=f'Bearer {token}').status_code, 401)

    def test_token_expires(self):
        _, body = self.dispatch()
        DiagnosticReport.objects.filter(pk=self.report.pk).update(
            symbolicate_requested_at=timezone.now() - timedelta(hours=3))
        resp = self.client.get(f'/api/diagnostics/symbolicate/{self.report.pk}/', secure=True,
                               HTTP_AUTHORIZATION=f"Bearer {body['inputs']['token']}")
        self.assertEqual(resp.status_code, 401)

    def test_workflow_error_is_stored(self):
        _, body = self.dispatch()
        self.client.post(f'/api/diagnostics/symbolicate/{self.report.pk}/', json.dumps({'error': 'No dSYM for 131'}),
                         content_type='application/json', secure=True,
                         HTTP_AUTHORIZATION=f"Bearer {body['inputs']['token']}")
        self.report.refresh_from_db()
        self.assertEqual((self.report.symbolicate_status, self.report.symbolicate_error), ('failed', 'No dSYM for 131'))

    @override_settings(GITHUB_SYMBOLICATE_TOKEN='')
    def test_not_configured(self):
        ok, message = symbolicate.request(self.report)
        self.assertFalse(ok)
        self.assertIn('GITHUB_SYMBOLICATE_TOKEN', message)


# ---------------------------------------------------------------------------
# The staff panel
# ---------------------------------------------------------------------------

@override_settings(API_KEY=KEY)
class PanelTests(TestCase):
    def setUp(self):
        cache.clear()
        self.staff = User.objects.create_user('staff', 'staff@example.com', 'pw', is_staff=True)
        self.organizer = User.objects.create_user('org', 'org@example.com', 'pw')
        self.client = TwoFactorClient()
        Client().post('/api/v2/diagnostics/logs/', json.dumps(batch([
            record(), record(seq=2, level='error', cat='ui', event='error.caught', msg='<b>boom</b>'),
            record(seq=3, cat='lifecycle', event='lifecycle.launch', f={'kind': 'cold', 'ms': 640}),
            record(seq=4, cat='push', event='push.received', f={'type': 'content_updated'}),
        ])), content_type='application/json', secure=True, HTTP_X_REQUEST_ID='abcd1234ef', **AUTH)
        Client().get('/api/v2/events/lookup/?q=X', secure=True, HTTP_X_REQUEST_ID='abcd1234ef',
                     HTTP_X_DIAG_SESSION=CODE, **AUTH)

    def urls(self):
        report = DiagnosticReport.objects.create(platform='ios', kind='hang', signature='s', payload=PAYLOAD)
        return [reverse('diagnostics:overview'), reverse('diagnostics:session', args=[CODE]),
                reverse('diagnostics:session_entries', args=[CODE]), reverse('diagnostics:session_export', args=[CODE]),
                reverse('diagnostics:reports'), reverse('diagnostics:report', args=[report.pk]),
                reverse('diagnostics:endpoints'), reverse('diagnostics:symbols')]

    def test_staff_only(self):
        urls = self.urls()
        for url in urls:
            self.assertEqual(Client().get(url).status_code, 302, url)  # sign in first
        self.client.force_login(self.organizer)
        for url in urls:
            self.assertEqual(self.client.get(url).status_code, 404, url)
        self.assertNotContains(self.client.get(reverse('console:dashboard')), 'Diagnostics')
        self.client.force_login(self.staff)
        for url in urls:
            self.assertEqual(self.client.get(url).status_code, 200, url)

    def test_staff_needs_the_second_factor(self):
        plain = Client()
        plain.force_login(self.staff)
        from urllib.parse import urlencode
        self.assertRedirects(plain.get(reverse('diagnostics:overview')),
                             reverse('two_factor_setup') + '?' + urlencode({'next': reverse('diagnostics:overview')}),
                             fetch_redirect_response=False)

    def test_overview_search_and_session_page(self):
        self.client.force_login(self.staff)
        page = self.client.get(reverse('diagnostics:overview'), {'q': '7k3', 'errors': '1', 'platform': 'ios'})
        self.assertContains(page, CODE)
        self.assertNotContains(self.client.get(reverse('diagnostics:overview'), {'q': 'ZZZZZ'}), f'>{CODE}<')
        session = self.client.get(reverse('diagnostics:session', args=[CODE]))
        self.assertContains(session, 'error.caught')
        self.assertContains(session, '&lt;b&gt;boom&lt;/b&gt;')  # app text is escaped
        self.assertContains(session, '640 ms')
        self.assertContains(session, 'Pushes handled / received')

    def test_entries_json_interleaves_server_lines_by_request_id(self):
        self.client.force_login(self.staff)
        data = self.client.get(reverse('diagnostics:session_entries', args=[CODE])).json()
        names = [e['e'] for e in data['entries']]
        self.assertIn('server.request', names)
        app = next(e for e in data['entries'] if e['e'] == 'net.request.finished')
        server = next(e for e in data['entries'] if e['e'] == 'server.request')
        self.assertEqual(names.index('server.request'), names.index('net.request.finished') + 1)
        self.assertEqual(server['k'], app['k'])

    def test_export_jsonl(self):
        self.client.force_login(self.staff)
        resp = self.client.get(reverse('diagnostics:session_export', args=[CODE]))
        lines = [json.loads(line) for line in b''.join(resp.streaming_content).decode().splitlines()]
        self.assertEqual(len(lines), 5)
        self.assertTrue(all(line['v'] == 1 and line['diag'] == CODE for line in lines))
        self.assertEqual({line.get('source') for line in lines}, {None, 'server'})

    def test_delete_session(self):
        self.client.force_login(self.staff)
        self.client.post(reverse('diagnostics:session_delete', args=[CODE]))
        self.assertFalse(DiagnosticSession.objects.exists())

    def test_android_report_is_retraced_with_its_mapping(self):
        SymbolFile.objects.create(app_version='2.0.131', build='131000', data=gzip.compress(MAPPING.encode()),
                                  size=len(MAPPING), sha256='0')
        report = DiagnosticReport.objects.create(platform='android', kind='crash', build='131000', signature='s',
                                                 stack=STACK)
        self.client.force_login(self.staff)
        page = self.client.get(reverse('diagnostics:report', args=[report.pk]))
        self.assertContains(page, 'com.arcu.attnd.ui.EventViewModel.refresh(EventViewModel.kt:42)')
        page = self.client.post(reverse('diagnostics:symbols'), {'mapping': SymbolFile.objects.get().pk,
                                                                  'stack': STACK, 'action': 'retrace'})
        self.assertContains(page, 'EventRepository.load(EventRepository.kt:88)')

    @override_settings(GITHUB_SYMBOLICATE_TOKEN='')
    def test_symbolicate_button_explains_the_missing_token(self):
        report = DiagnosticReport.objects.create(platform='ios', kind='crash', signature='s', payload=PAYLOAD)
        self.client.force_login(self.staff)
        self.assertContains(self.client.get(reverse('diagnostics:report', args=[report.pk])), 'GITHUB_SYMBOLICATE_TOKEN')
        resp = self.client.post(reverse('diagnostics:report', args=[report.pk]), {'action': 'symbolicate'}, follow=True)
        self.assertContains(resp, 'is not set')

    def test_pages_follow_the_csp(self):
        self.client.force_login(self.staff)
        for url in self.urls():
            resp = self.client.get(url)
            if resp.get('Content-Type', '').startswith('text/html'):
                html = resp.content.decode()
                for attrs in re.findall(r'<script\b([^>]*)>', html):
                    self.assertTrue('src=' in attrs or 'nonce=' in attrs, f'{url}: <script{attrs}>')
                self.assertIsNone(re.search(r'<[a-zA-Z][^>]*\son[a-z]+\s*=', html), url)
                self.assertIn("default-src 'self'", resp['Content-Security-Policy'])


# ---------------------------------------------------------------------------
# API-014: anonymous performance metrics in the usage batch
# ---------------------------------------------------------------------------

@override_settings(API_KEY=KEY)
class PerformanceUsageTests(TestCase):
    def test_bucketed_metrics(self):
        event = Event.objects.create(name='Fair', event_code='FAIR', start_time=timezone.now(), timezone='UTC')
        entries = [{'name': 'perf_launch_cold', 'detail': '<1s'}, {'name': 'perf_launch_warm', 'detail': '>=4s'},
                   {'name': 'perf_bundle_fetch', 'detail': '<250ms', 'count': 3}, {'name': 'perf_bundle_304', 'detail': '<1s'},
                   {'name': 'perf_api_error', 'detail': 'timeout'}, {'name': 'perf_push_refresh_delay', 'detail': '<15s'},
                   {'name': 'perf_live_start_fail'}, {'name': 'perf_live_start_fail', 'detail': 'denied'},
                   {'name': 'perf_planner_ms', 'detail': '<500ms'}, {'name': 'perf_map_first_render', 'detail': '<2s'},
                   # dropped: unknown bucket, missing bucket, made-up metric
                   {'name': 'perf_launch_cold', 'detail': '<3s'}, {'name': 'perf_bundle_fetch'}, {'name': 'perf_x'}]
        resp = self.client.post(f'/api/v2/events/{event.id}/usage/', json.dumps({'platform': 'ios', 'events': entries}),
                                content_type='application/json', secure=True, **AUTH)
        self.assertEqual(resp.json()['accepted'], 10)
        self.assertEqual(UsageCount.objects.get(metric='perf_bundle_fetch').count, 3)
        self.assertEqual(set(UsageCount.objects.filter(metric='perf_live_start_fail').values_list('detail', flat=True)),
                         {'', 'denied'})
        staff = User.objects.create_user('staff', 's@example.com', 'pw', is_staff=True)
        client = TwoFactorClient()
        client.force_login(staff)
        self.assertContains(client.get(reverse('diagnostics:overview')), 'perf_bundle_fetch')


# ---------------------------------------------------------------------------
# The shared event catalogue
# ---------------------------------------------------------------------------

class EventCatalogueTests(SimpleTestCase):
    def test_log_events_md_is_well_formed(self):
        text = (Path(settings.BASE_DIR) / 'LOG_EVENTS.md').read_text()
        rows = re.findall(r'^\| `([a-z0-9_.]+)` \| `([a-z]+)` \|', text, re.M)
        self.assertGreater(len(rows), 60)
        names = [name for name, _ in rows]
        self.assertEqual(len(names), len(set(names)), 'duplicate event names')
        for name, cat in rows:
            self.assertRegex(name, records.EVENT_RE, name)
            self.assertIn(cat, (*APP_CATEGORIES, 'server'), name)
        for required in ('lifecycle.launch', 'net.request.started', 'net.request.finished', 'net.request.failed',
                         'push.received', 'push.handled', 'background.refresh.started', 'background.refresh.finished',
                         'live.start', 'live.update', 'live.end', 'live.token', 'location.permission', 'planner.run',
                         'assistant.request', 'storage.bundle.saved', 'perf.signpost', 'ui.screen', 'error.caught',
                         'server.request'):
            self.assertIn(required, names)
