"""IndexNow (attnd/indexnow.py): production only, queued on changes, one batched POST per host, key files."""
from unittest.mock import MagicMock, patch

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone

from attnd import indexnow
from events.models import Event

KEY = 'a1b2c3d4e5f6a7b8'
PROD = dict(INDEXNOW_KEY=KEY, SITE_URL='https://attnd.app')


@override_settings(**PROD)
class IndexNowTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user('o', 'o@example.com', 'pw')
        self.event = Event.objects.create(name='Fair', event_code='FAIR7', start_time=timezone.now(), user=self.user,
                                          status=Event.STATUS_PUBLISHED, public=False, paid=True)

    def queued(self):
        return cache.get(indexnow.QUEUE) or []

    def test_only_on_production_with_a_key(self):
        with override_settings(SITE_URL='https://dev.attnd.app'):
            self.assertFalse(indexnow.enabled())
            self.assertEqual(indexnow.queue('https://dev.attnd.app/join/X/'), 0)
        with override_settings(INDEXNOW_KEY='bad key!'):
            self.assertFalse(indexnow.enabled())
        self.assertTrue(indexnow.enabled())

    def test_listing_and_unlisting_queue_the_join_page(self):
        self.event.public = True
        self.event.save()
        self.assertEqual(self.queued(), ['https://attnd.app/join/FAIR7/'])
        cache.delete(indexnow.QUEUE)
        self.event.public = False  # unlisted: engines should drop it
        self.event.save()
        self.assertEqual(self.queued(), ['https://attnd.app/join/FAIR7/'])
        cache.delete(indexnow.QUEUE)
        self.event.paid = False  # never indexable before or after: nothing
        self.event.save()
        self.assertEqual(self.queued(), [])

    def test_test_events_never(self):
        Event.objects.filter(pk=self.event.pk).update(testing_only=True)
        event = Event.objects.get(pk=self.event.pk)
        event.public = True
        event.save()
        self.assertEqual(self.queued(), [])

    def test_flush_batches_per_host(self):
        indexnow.queue('https://attnd.app/join/A/', 'https://attnd.app/join/B/', 'https://attnd.site/fair/',
                       'https://attnd.app/join/A/')
        ok = MagicMock(status_code=202)
        with patch('httpx.post', return_value=ok) as post:
            self.assertEqual(indexnow.flush(), 3)
        bodies = {c.kwargs['json']['host']: c.kwargs['json'] for c in post.call_args_list}
        self.assertEqual(bodies['attnd.app']['urlList'], ['https://attnd.app/join/A/', 'https://attnd.app/join/B/'])
        self.assertEqual(bodies['attnd.site']['keyLocation'], f'https://attnd.site/{KEY}.txt')
        self.assertEqual(self.queued(), [])
        self.assertEqual(indexnow.usage()['urls'], 3)

    def test_a_server_error_is_retried_once(self):
        indexnow.queue('https://attnd.app/join/A/')
        with patch('httpx.post', return_value=MagicMock(status_code=503)):
            self.assertEqual(indexnow.flush(), 0)
        self.assertEqual(self.queued(), ['https://attnd.app/join/A/'])
        with patch('httpx.post', return_value=MagicMock(status_code=503)):
            indexnow.flush()
        self.assertEqual(self.queued(), [])  # once only
        self.assertEqual(indexnow.usage()['errors'], 2)

    def test_key_file(self):
        resp = self.client.get(f'/{KEY}.txt', secure=True)
        self.assertEqual((resp.status_code, resp.content.decode()), (200, KEY))
        self.assertEqual(self.client.get('/someotherkey1.txt', secure=True).status_code, 404)
        with override_settings(SITE_URL='https://dev.attnd.app'):
            self.assertEqual(self.client.get(f'/{KEY}.txt', secure=True).status_code, 404)
