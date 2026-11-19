"""API-019: App Clip devices (`app_clip` in join/ and devices/) get their pushes on the clip's APNs topic."""
from unittest.mock import patch

from django.test import override_settings

from events import push
from events.models import Device, Notification
from events.tests_hearts_tags_urgent import Base

CFG = {'key': 'k', 'key_id': 'i', 'team': 't', 'topic': 'com.arcu.Attnd', 'host': 'https://apns.test'}


@override_settings(API_KEY='k-glance')
class AppClipFlagTests(Base):
    def device(self):
        return Device.objects.get(pk='clip-1')

    def test_join_and_devices_store_the_flag_and_a_missing_key_keeps_it(self):
        join = f'/api/v2/events/{self.event.id}/join/'
        self.post(join, {'device_id': 'clip-1', 'platform': 'ios', 'app_clip': True})
        self.assertTrue(self.device().app_clip)
        self.post('/api/v2/devices/', {'device_id': 'clip-1', 'push_token': 'tok'})  # missing: unchanged
        self.assertTrue(self.device().app_clip)
        resp = self.post('/api/v2/devices/', {'device_id': 'clip-1', 'app_clip': 'yes'})  # not a boolean: ignored
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(self.device().app_clip)
        self.post(join, {'device_id': 'clip-1', 'app_clip': None})
        self.assertTrue(self.device().app_clip)
        self.post('/api/v2/devices/', {'device_id': 'clip-1', 'app_clip': False})
        self.assertFalse(self.device().app_clip)

    def test_default_is_the_app(self):
        self.post('/api/v2/devices/', {'device_id': 'clip-1', 'platform': 'ios'})
        self.assertFalse(self.device().app_clip)


@override_settings(API_KEY='k-glance')
class AppClipTopicTests(Base):
    def setUp(self):
        super().setUp()
        Device.objects.create(id='app', event=self.event, platform='ios', push_token='t-app', live_activity_token='la-app')
        Device.objects.create(id='clip', event=self.event, platform='ios', push_token='t-clip', app_clip=True,
                              live_activity_token='la-clip')
        Device.objects.create(id='clip-off', event=self.event, platform='ios', push_token='t-clip-off', app_clip=True,
                              subscribed=False)
        Device.objects.create(id='and', event=self.event, platform='android', push_token='t-and')
        self.calls = []

    def apns(self, tokens, payload, **kwargs):
        self.calls.append((sorted(tokens), kwargs.get('topic'), kwargs.get('push_type', 'alert')))
        invalid = [t for t in tokens if t in getattr(self, 'invalid', ())]
        return push.SendResult(delivered=len(tokens) - len(invalid), failed=len(invalid), invalid_tokens=invalid)

    def run_push(self, fn):
        with patch('events.push.send_apns', side_effect=self.apns), \
                patch('events.push.send_fcm', return_value=push.SendResult()), \
                patch('events.push._apns_config', return_value=CFG):
            return fn()

    def notification(self, priority='normal'):
        return Notification.objects.create(event=self.event, user=self.owner, title='T', body='B',
                                           send_time=self.event.start_time, priority=priority)

    def test_topics(self):
        self.assertEqual(push.apns_topic(CFG), 'com.arcu.Attnd')
        self.assertEqual(push.apns_topic(CFG, app_clip=True), 'com.arcu.Attnd.clip')
        self.assertEqual(push.apns_topic(CFG, live_activity=True), 'com.arcu.Attnd.push-type.liveactivity')
        self.assertEqual(push.apns_topic(CFG, app_clip=True, live_activity=True),
                         'com.arcu.Attnd.clip.push-type.liveactivity')
        self.assertIsNone(push.apns_topic(None, app_clip=True))
        with self.settings(APNS_CLIP_TOPIC='com.example.clip'):
            self.assertEqual(push.apns_topic(CFG, app_clip=True, live_activity=True),
                             'com.example.clip.push-type.liveactivity')

    def test_organizer_notification_per_topic(self):
        n = self.run_push(lambda: push.send_notification(self.notification()))
        self.assertEqual(self.calls, [(['t-app'], 'com.arcu.Attnd', 'alert'),
                                      (['t-clip'], 'com.arcu.Attnd.clip', 'alert')])  # subscribed only
        self.assertEqual(n.delivered_count, 2)

    def test_content_updated_per_topic(self):
        self.run_push(lambda: push.notify_content_updated(self.event))
        self.assertEqual(self.calls, [(['t-app'], 'com.arcu.Attnd', 'background'),
                                      (['t-clip', 't-clip-off'], 'com.arcu.Attnd.clip', 'background')])

    def test_urgent_alert_and_live_activity_per_topic(self):
        self.run_push(lambda: push.send_notification(self.notification('urgent')))
        self.assertEqual(self.calls, [
            (['t-app'], 'com.arcu.Attnd', 'alert'),
            (['t-clip', 't-clip-off'], 'com.arcu.Attnd.clip', 'alert'),
            (['la-app'], 'com.arcu.Attnd.push-type.liveactivity', 'liveactivity'),
            (['la-clip'], 'com.arcu.Attnd.clip.push-type.liveactivity', 'liveactivity'),
        ])

    def test_only_app_devices_send_no_clip_request(self):
        Device.objects.filter(app_clip=True).delete()
        self.run_push(lambda: push.send_notification(self.notification('urgent')))
        self.assertEqual([c[1] for c in self.calls], ['com.arcu.Attnd', 'com.arcu.Attnd.push-type.liveactivity'])

    def test_invalid_tokens_are_forgotten_in_each_group(self):
        self.invalid = {'t-clip', 'la-clip', 't-app'}
        self.run_push(lambda: push.send_notification(self.notification('urgent')))
        tokens = dict(Device.objects.values_list('id', 'push_token'))
        self.assertEqual((tokens['app'], tokens['clip'], tokens['clip-off']), ('', '', 't-clip-off'))
        la = dict(Device.objects.values_list('id', 'live_activity_token'))
        self.assertEqual((la['app'], la['clip']), ('la-app', ''))
