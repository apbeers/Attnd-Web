"""API-025: the free limit. An unpaid event takes FREE_DEVICE_LIMIT different devices on join; rejoining always works;
paid, showcase, DEMO and the App Review sample events are exempt; CI and showcase-test devices never count."""

from datetime import timedelta

from django.contrib.auth.models import User
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from events import free_limit
from events.models import Device, Event, EventJoin

AUTH = {'HTTP_AUTHORIZATION': 'Bearer limit-key'}


@override_settings(API_KEY='limit-key', FREE_DEVICE_LIMIT=3, CI_API_KEYS=['c' * 40])
class FreeLimitTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user('o', 'o@example.com', 'pw')
        start = timezone.now() + timedelta(hours=1)
        self.event = Event.objects.create(name='Demo try', event_code='TRYIT', user=self.owner, start_time=start,
                                          status=Event.STATUS_PUBLISHED, timezone='UTC')
        self.client = Client()

    def join(self, device, event=None, key='limit-key'):
        e = event or self.event
        return self.client.post(f'/api/v2/events/{e.pk}/join/', {'device_id': device, 'platform': 'ios'},
                                content_type='application/json', secure=True, HTTP_AUTHORIZATION=f'Bearer {key}')

    def test_the_limit_and_rejoin(self):
        for i in range(3):
            self.assertEqual(self.join(f'dev-{i}').status_code, 200)
        full = self.join('dev-new')
        self.assertEqual(full.status_code, 409)
        self.assertEqual(full.json()['error'], 'event_full_free')
        self.assertEqual(full.json()['limit'], 3)
        self.assertIn('full', full.json()['message'])
        self.assertFalse(Device.objects.filter(id='dev-new').exists(), 'a refused device is not registered')
        # Leaving and coming back is still the same device: always allowed.
        self.client.post(f'/api/v2/events/{self.event.pk}/leave/', {'device_id': 'dev-0'},
                         content_type='application/json', secure=True, **AUTH)
        self.assertEqual(self.join('dev-0').status_code, 200)
        self.assertEqual(self.join('dev-1').status_code, 200)
        self.assertEqual(free_limit.used(self.event), 3)

    def test_paid_showcase_demo_and_review_events_are_exempt(self):
        for flag in ('paid', 'showcase'):
            e = Event.objects.create(name=flag, event_code=f'X{flag.upper()}', status=Event.STATUS_PUBLISHED,
                                     timezone='UTC', start_time=timezone.now(), **{flag: True})
            for i in range(5):
                self.assertEqual(self.join(f'{flag}-{i}', e).status_code, 200, flag)
        for code in ('DEMO', 'FWFM2026'):
            e = Event.objects.create(name=code, event_code=code, status=Event.STATUS_PUBLISHED, timezone='UTC', start_time=timezone.now())
            self.assertTrue(free_limit.exempt(e))
            self.assertEqual(free_limit.status(e), {'limited': False})

    def test_marking_paid_lifts_it(self):
        for i in range(3):
            self.join(f'dev-{i}')
        self.assertEqual(self.join('late').status_code, 409)
        self.event.paid = True
        self.event.save()
        self.assertEqual(self.join('late').status_code, 200)

    def test_test_devices_never_count(self):
        for i in range(5):
            EventJoin.objects.create(event=self.event, device_id=f'ci-android-{i}')
            EventJoin.objects.create(event=self.event, device_id=f'showcase-check-{i}')
        self.assertEqual(free_limit.used(self.event), 0)
        self.assertEqual(free_limit.status(self.event), {'limited': True, 'used': 0, 'limit': 3, 'left': 3, 'full': False})
        self.assertTrue(free_limit.admit(self.event, 'showcase-check-99'))

    def test_console_shows_the_spots_and_the_request(self):
        for i in range(2):
            self.join(f'dev-{i}')
        c = Client()
        c.force_login(self.owner)
        html = c.get(reverse('console:event', args=[self.event.pk]), secure=True).content.decode()
        self.assertIn('2 of 3 free spots used', html)
        self.assertIn('Remove the limit', html)
        r = c.post(reverse('console:event_limit_request', args=[self.event.pk]), {'message': 'About 400 people'}, secure=True)
        self.assertEqual(r.status_code, 302)
        # Organizers can't list an unpaid event nearby; staff mark it paid.
        c.post(reverse('console:event_status', args=[self.event.pk]), {'action': 'list'}, secure=True)
        self.event.refresh_from_db()
        self.assertFalse(self.event.public)
        self.assertEqual(c.post(reverse('console:event_paid', args=[self.event.pk]), {'paid': '1'}, secure=True).status_code, 404)
        staff = User.objects.create_user('s', 's@example.com', 'pw', is_staff=True)
        from console.testing import TwoFactorClient
        sc = TwoFactorClient()
        sc.force_login(staff)
        self.event.user = staff
        self.event.save()
        self.assertEqual(sc.post(reverse('console:event_paid', args=[self.event.pk]), {'paid': '1'}, secure=True).status_code, 302)
        self.event.refresh_from_db()
        self.assertTrue(self.event.paid)

    def test_existing_devices_were_backfilled_as_joined(self):
        # The migration recorded every device already in an event (so nobody is refused after the deploy).
        from importlib import import_module
        migration = import_module('events.migrations.0045_showcase_joins')
        from django.apps import apps
        Device.objects.create(id='old-phone', platform='ios', event=self.event)
        migration.backfill(apps, None)
        self.assertTrue(EventJoin.objects.filter(event=self.event, device_id='old-phone').exists())
