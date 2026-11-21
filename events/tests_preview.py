"""API-026: faster live updates (immediate push, jitter_ms) and the organizer's preview phone."""
import json
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from console.testing import TwoFactorClient
from events import preview, push
from events.models import Device, Event, EventJoin
from events.push import SendResult

AUTH = {'HTTP_AUTHORIZATION': 'Bearer test-key'}


@override_settings(API_KEY='test-key', FREE_DEVICE_LIMIT=1)
class PreviewTestCase(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user('o', 'o@example.com', 'pw')
        self.stranger = User.objects.create_user('s', 's@example.com', 'pw')
        self.event = Event.objects.create(name='Fair', event_code='FAIR2026', user=self.owner, timezone='America/Chicago',
                                          start_time=timezone.now() + timedelta(hours=2))
        self.api = Client()

    def join(self, device, **extra):
        return self.api.post(f'/api/v2/events/{self.event.pk}/join/', json.dumps({'device_id': device, 'platform': 'ios', **extra}),
                             content_type='application/json', secure=True, **AUTH)

    def changes(self, device, since, **kw):
        return self.api.get(f'/api/v2/events/{self.event.pk}/changes/?since={since}', secure=True,
                            HTTP_X_DEVICE_ID=device, **AUTH, **kw)


class JoinTests(PreviewTestCase):
    def test_preview_token_makes_a_preview_phone_outside_the_free_limit(self):
        self.assertEqual(self.join('attendee-1').status_code, 200)  # the one free spot
        token = preview.make_token(self.owner, self.event)
        answer = self.join('organizer-phone', preview_token=token)
        self.assertEqual(answer.status_code, 200)
        self.assertTrue(answer.json()['preview'])
        phone = Device.objects.get(pk='organizer-phone')
        self.assertEqual(phone.preview_by, self.owner)
        self.assertGreater(phone.preview_until, timezone.now() + timedelta(days=6))
        self.assertEqual(list(Device.objects.attendees().values_list('pk', flat=True)), ['attendee-1'])
        self.assertEqual(self.join('attendee-2').status_code, 409)  # the free limit still holds for attendees

    def test_bad_tokens_just_join(self):
        other = Event.objects.create(name='Other', event_code='OTHER', user=self.owner, timezone='UTC',
                                     start_time=timezone.now())
        for token in ('nonsense', preview.make_token(self.owner, other), preview.make_token(self.stranger, self.event)):
            answer = self.join(f'p-{len(token)}', preview_token=token)
            self.assertNotIn('preview', answer.json())
        self.assertFalse(Device.objects.previews().exists())
        with patch('django.core.signing.time.time', return_value=timezone.now().timestamp() - 8 * 86400):
            old = preview.make_token(self.owner, self.event)
        self.assertIsNone(preview.read_token(old, self.event))

    def test_an_attendee_turning_preview_frees_its_spot_and_at_most_three_are_live(self):
        self.join('phone-0')
        self.assertTrue(EventJoin.objects.filter(device_id='phone-0').exists())
        token = preview.make_token(self.owner, self.event)
        for i in range(5):
            self.join(f'phone-{i}', preview_token=token)
        self.assertFalse(EventJoin.objects.filter(device_id='phone-0').exists())
        self.assertEqual(Device.objects.previews().filter(event=self.event).count(), 3)

    def test_leaving_or_switching_ends_it(self):
        self.join('p', preview_token=preview.make_token(self.owner, self.event))
        self.api.post(f'/api/v2/events/{self.event.pk}/leave/', json.dumps({'device_id': 'p'}),
                      content_type='application/json', secure=True, **AUTH)
        self.assertIsNone(Device.objects.get(pk='p').preview_until)


class ChangesTests(PreviewTestCase):
    def setUp(self):
        super().setUp()
        self.join('p', preview_token=preview.make_token(self.owner, self.event))
        self.version = Event.objects.get(pk=self.event.pk).content_version

    def test_answers_at_once_when_behind(self):
        body = self.changes('p', self.version - 1).json()
        self.assertEqual((body['changed'], body['preview'], body['retry_after']), (True, True, 0))

    def test_waits_then_answers_and_gives_the_connection_back(self):
        with patch.object(preview, 'WAIT_SECONDS', 0.2), patch.object(preview, 'CHECK_EVERY', 0.05), \
                patch('django.db.connection.in_atomic_block', False), patch('django.db.connection.close') as close:
            body = preview.wait_for_change(self.event.pk, self.version, seconds=0.2)
        self.assertEqual(body, (self.version, False, True))
        self.assertGreater(close.call_count, 0)

    def test_attendees_and_strangers_are_told_to_poll_normally(self):
        self.join('attendee')
        for device in ('attendee', 'nobody', ''):
            body = self.changes(device, self.version).json()
            self.assertEqual((body['preview'], body['retry_after']), (False, 300))

    def test_caps(self):
        from events import longpoll
        with patch('django.db.connection.in_atomic_block', False):
            taken = [longpoll.acquire() for _ in range(longpoll.MAX_WAITING)]
            try:
                self.assertEqual(preview.wait_for_change(self.event.pk, self.version), (self.version, False, False))
            finally:
                for ok in taken:
                    if ok:
                        longpoll.release()
            with patch.dict(preview._per_event, {self.event.pk: preview.MAX_DEVICES}):
                self.assertEqual(preview.wait_for_change(self.event.pk, self.version), (self.version, False, False))


class PushTests(PreviewTestCase):
    def test_jitter_scales_with_the_event(self):
        self.assertEqual([push.jitter_ms(n) for n in (0, 99, 100, 999, 1000)], [1000, 1000, 5000, 5000, 10000])

    def test_attendees_get_jitter_and_preview_phones_none(self):
        self.join('a', push_token='tok-a')
        self.join('p', push_token='tok-p', preview_token=preview.make_token(self.owner, self.event))
        calls = []

        def fake(event, data=None, silent=False, devices=None, **kw):
            calls.append((sorted(devices.filter(event=event).values_list('pk', flat=True)), data['jitter_ms']))
            return SendResult(delivered=1)
        with patch('events.push.deliver', side_effect=fake):
            push.notify_content_updated(self.event)
        self.assertEqual(calls, [(['a'], 1000), (['p'], 0)])

    def test_first_change_goes_out_at_once_then_one_grouped_follow_up(self):
        with self.settings(CONTENT_PUSH_DELAY=0, CONTENT_PUSH_MIN_INTERVAL=15), \
                patch('events.push.threading.Timer') as timer:
            push._last_sent.pop(self.event.pk, None)
            push._schedule(self.event.pk)
            self.assertEqual(timer.call_args.args[0], 0)  # at once
            push._timers.pop(self.event.pk)
            push._last_sent[self.event.pk] = push.time.monotonic()
            push._schedule(self.event.pk)
            self.assertGreater(timer.call_args.args[0], 14)  # the rest of the 15 s
            push._schedule(self.event.pk)
            self.assertEqual(timer.call_count, 2)  # joined the waiting follow-up
            push._timers.pop(self.event.pk, None)


class ConsoleTests(PreviewTestCase):
    def setUp(self):
        super().setUp()
        self.console = TwoFactorClient()
        self.console.force_login(self.owner)

    def test_bundle_records_the_version_and_the_card_says_how_many_phones_have_it(self):
        self.join('a1')
        Device.objects.filter(pk='a1').update(last_seen=timezone.now())
        self.assertEqual(preview.phones(self.event)['text'], 'No attendee phones have checked in yet.')  # no version reported yet
        Device.objects.filter(pk='a1').update(last_version=self.event.content_version - 1)
        self.assertEqual(preview.phones(self.event)['text'], 'Updated on 0 of 1 phone.')
        self.api.get(f'/api/v2/events/{self.event.pk}/bundle/', secure=True, HTTP_X_DEVICE_ID='a1', **AUTH)
        self.assertEqual(Device.objects.get(pk='a1').last_version, self.event.content_version)
        self.assertEqual(preview.phones(self.event)['text'], 'Up to date on the 1 phone.')
        self.assertEqual(self.console.get(reverse('console:event_phones', args=[self.event.pk]), secure=True).json()['updated'], 1)

    def test_preview_qr_card_and_remove(self):
        self.join('p', preview_token=preview.make_token(self.owner, self.event))
        page = self.console.get(reverse('console:event', args=[self.event.pk]), secure=True, follow=True)
        self.assertContains(page, 'Preview on my phone')
        self.assertContains(page, 'Open as preview')
        qr = self.console.get(reverse('console:event_preview_qr', args=[self.event.pk]), secure=True)
        self.assertEqual(qr['Content-Type'], 'image/png')
        self.console.post(reverse('console:event_preview_remove', args=[self.event.pk]), {'device': 'p'}, secure=True)
        device = Device.objects.get(pk='p')
        self.assertEqual((device.event_id, device.preview_until), (None, None))
        stranger = TwoFactorClient()
        stranger.force_login(self.stranger)
        self.assertEqual(stranger.get(reverse('console:event_preview_qr', args=[self.event.pk]), secure=True).status_code, 404)
