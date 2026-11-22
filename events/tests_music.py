"""API-030: Apple Music artist links (events/music.py)."""
import urllib.error
from datetime import timedelta
from unittest import mock

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from console.testing import TwoFactorClient
from events import music
from events.models import Event, ScheduledItem

KEY = ec.generate_private_key(ec.SECP256R1())
PEM = KEY.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                        serialization.NoEncryption()).decode()
ON = dict(APPLE_MUSIC_KEY_ID='ABC123DEF4', APPLE_MUSIC_PRIVATE_KEY=PEM, APPLE_TEAM_ID='TEAM123456')


def artist(id_, name, genres=(), url=None):
    return {'id': id_, 'type': 'artists', 'attributes': {
        'name': name, 'genreNames': list(genres), 'url': url or f'https://music.apple.com/us/artist/x/{id_}'}}


class MatchTests(TestCase):
    def test_names_must_be_equal_after_normalising(self):
        self.assertEqual(music.norm('The Prairie Lights!'), 'prairie lights')
        self.assertEqual(music.norm('Simon & Garfunkel'), music.norm('Simon and Garfunkel'))
        self.assertEqual(music.norm('Beyoncé'), 'beyonce')
        got = music.match('Prairie Lights', [], [artist('1', 'The Prairie Lights')])
        self.assertEqual(got['id'], '1')
        self.assertIsNone(music.match('Prairie Lights', [], [artist('1', 'Prairie Lights Tribute Band')]))

    def test_genres_decide_or_refuse(self):
        two = [artist('1', 'Echo', ['Classical']), artist('2', 'Echo', ['Rock', 'Alternative'])]
        self.assertIsNone(music.match('Echo', [], two))  # two artists of that name, nothing to tell them apart
        self.assertEqual(music.match('Echo', ['indie rock'], two)['id'], '2')
        self.assertIsNone(music.match('Echo', ['jazz'], [artist('1', 'Echo', ['Classical'])]))
        self.assertEqual(music.match('Echo', ['jazz'], [artist('1', 'Echo')])['id'], '1')  # Apple gave no genres

    def test_only_apple_music_links(self):
        self.assertIsNone(music.match('Echo', [], [artist('1', 'Echo', url='https://evil.example/x')]))
        self.assertTrue(music.valid_url('https://music.apple.com/us/artist/echo/1'))
        self.assertFalse(music.valid_url('http://music.apple.com/us/artist/echo/1'))


@override_settings(**ON)
class LookupTests(TestCase):
    def setUp(self):
        cache.clear()
        music._token.update(value='', exp=0)

    def test_developer_token_is_es256_with_the_key_id(self):
        import jwt
        token = music.developer_token()
        self.assertEqual(jwt.get_unverified_header(token), {'alg': 'ES256', 'kid': 'ABC123DEF4', 'typ': 'JWT'})
        claims = jwt.decode(token, KEY.public_key(), algorithms=['ES256'])
        self.assertEqual(claims['iss'], 'TEAM123456')
        self.assertLessEqual(claims['exp'] - claims['iat'], 12 * 3600)

    def test_cached_counted_and_paused_on_429(self):
        with mock.patch.object(music, '_search', return_value=[artist('7', 'Echo')]) as search:
            self.assertEqual(music.lookup('Echo')['id'], '7')
            self.assertEqual(music.lookup('echo')['id'], '7')
        self.assertEqual(search.call_count, 1)
        n = music.usage()
        self.assertEqual((n['calls'], n['hits'], n['found']), (1, 1, 1))
        error = urllib.error.HTTPError('u', 429, 'slow down', {}, None)
        with mock.patch.object(music, '_search', side_effect=error):
            with self.assertRaises(music.Paused):
                music.lookup('Other')
        with mock.patch.object(music, '_search') as search:
            with self.assertRaises(music.Paused):
                music.lookup('Third')
            search.assert_not_called()
        self.assertTrue(music.paused())


class ItemTests(TestCase):
    def setUp(self):
        cache.clear()
        self.owner = User.objects.create_user('o', 'o@example.com', 'pw')
        now = timezone.now()
        self.event = Event.objects.create(name='Fest', event_code='MUSIC1', user=self.owner, timezone='UTC',
                                          start_time=now, end_time=now + timedelta(hours=8),
                                          status=Event.STATUS_PUBLISHED)
        self.item = ScheduledItem.objects.create(event=self.event, name='Main set', start_time=now, context={
            'performers': [{'name': 'Echo', 'genres': ['rock']}, {'name': 'DJ Nobody'}]})

    def search(self, term):
        return {'echo': [artist('7', 'Echo', ['Rock'])]}.get(music.norm(term), [])

    def test_off_without_keys(self):
        self.assertFalse(music.enabled())
        self.assertEqual(music.refresh_item(self.item), (0, 0))

    @override_settings(**ON)
    def test_links_reach_the_bundle_and_renames_are_looked_up_again(self):
        from events.content import item_v2
        with mock.patch.object(music, '_search', side_effect=self.search) as search:
            version = Event.objects.get(pk=self.event.pk).content_version
            self.assertEqual(music.refresh_item(self.item), (2, 1))
            self.assertGreater(Event.objects.get(pk=self.event.pk).content_version, version)
            self.item.refresh_from_db()
            performers = item_v2(self.item)['context']['performers']
            self.assertEqual(performers[0]['apple_music_url'], 'https://music.apple.com/us/artist/x/7')
            self.assertNotIn('apple_music_url', performers[1])
            self.assertEqual(music.refresh_item(self.item), (0, 0))  # nothing new: no lookups
            self.item.context = {'performers': [{'name': 'Echo', 'genres': ['rock']}, {'name': 'DJ Somebody'}]}
            self.item.save()
            self.assertEqual(music.wanted(self.item), [{'name': 'DJ Somebody'}])
            calls = search.call_count
            self.assertEqual(music.refresh_item(self.item), (1, 0))
            self.assertEqual(search.call_count, calls + 1)
            self.assertEqual(set(self.item.music), {'echo', 'dj somebody'})  # the old name's entry went

    @override_settings(**ON)
    def test_the_organizers_links_are_kept(self):
        music.set_links(self.item, {'Echo': 'https://music.apple.com/us/artist/mine/1', 'DJ Nobody': ''})
        self.item.save()
        with mock.patch.object(music, '_search', side_effect=self.search) as search:
            self.assertEqual(music.refresh_item(self.item, force=True), (0, 0))
            search.assert_not_called()
        self.assertEqual(music.links(self.item), {'echo': 'https://music.apple.com/us/artist/mine/1'})

    @override_settings(**ON)
    def test_console_form_sets_and_clears_links(self):
        from console.forms.event_forms import ScheduledItemForm
        self.item.music = {'echo': {'url': 'https://music.apple.com/us/artist/x/7', 'id': '7', 'at': 'x'}}
        self.item.save()
        form = ScheduledItemForm(instance=self.item, event=self.event)
        text = form.initial['ctx_performers']
        self.assertIn('Echo | rock | https://music.apple.com/us/artist/x/7', text)
        data = {'name': 'Main set', 'start_time': timezone.localtime(self.item.start_time).strftime('%Y-%m-%dT%H:%M'),
                'ctx_performers': text + ' \nDJ Nobody | | | no link'}
        bound = ScheduledItemForm(data, instance=self.item, event=self.event)
        self.assertTrue(bound.is_valid(), bound.errors)
        item = bound.save()
        self.assertNotIn('locked', item.music['echo'])  # unchanged automatic link stays automatic
        self.assertEqual(item.music['dj nobody']['locked'], True)
        self.assertEqual(item.music['dj nobody']['url'], '')
        bad = ScheduledItemForm({**data, 'ctx_performers': 'Echo | rock | | https://music.apple.evil/x'},
                                instance=self.item, event=self.event)
        self.assertFalse(bad.is_valid())

    @override_settings(**ON)
    def test_find_music_links_runs_in_the_background(self):
        client = TwoFactorClient()
        client.force_login(self.owner)
        with self.captureOnCommitCallbacks(execute=False) as callbacks:
            r = client.post(reverse('console:event_music_links', args=[self.event.pk]), HTTP_ACCEPT='application/json',
                            secure=True)
        self.assertEqual(r.status_code, 200, r.content)
        self.assertIn('2 performers', r.json()['message'])
        self.assertEqual(len(callbacks), 1)
        from console.components.catalog_chat import checks
        self.assertIn('music', [c['key'] for c in checks(self.event)])

    def test_find_music_links_says_when_it_is_off(self):
        client = TwoFactorClient()
        client.force_login(self.owner)
        r = client.post(reverse('console:event_music_links', args=[self.event.pk]), HTTP_ACCEPT='application/json',
                        secure=True)
        self.assertEqual(r.status_code, 400)
