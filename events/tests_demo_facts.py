"""The DEMO event as the apps' live integration tests see it.

The iOS, Android and web live suites (live_tests/) assert these facts against the real dev and production
servers. This test checks that load_demo_event still produces exactly them, so the generator and the
shared spec can't drift apart: change both (and the app suites) together.
"""
from datetime import datetime
from io import StringIO

from django.core.management import call_command
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from events.models import Event

LOCATION_NAMES = {
    'Main Stage', 'Trinity Stage', 'Panther Island Pavilion', 'Chef Demo Kitchen', 'Taco Row', 'BBQ Pit Lane',
    'Sweet Spot', 'Craft Beer Garden', 'Lemonade Stand', 'Makers Market', 'Kids Zone', 'First Aid', 'Info Booth',
    'Restrooms North', 'Restrooms South', 'Rideshare Pickup', 'Overflow Parking', 'Roaming Mariachi',
}
CATEGORIES = {'Stage', 'Food', 'Drinks', 'Vendor', 'Family', 'Services', 'Parking'}
SENT_TITLES = {'Welcome!', 'Taco Row is open', 'Shade & water', 'Set time change'}


@override_settings(API_KEY='demo-facts-key')
class DemoEventFactsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command('load_demo_event', '--devices', '3', stdout=StringIO())

    def api(self, path, **params):
        resp = Client().get(path, params, secure=True, HTTP_AUTHORIZATION='Bearer demo-facts-key')
        self.assertEqual(resp.status_code, 200, path)
        return resp.json()

    def test_lookup_any_case(self):
        ids = {self.api('/api/v2/events/lookup/', q=q)['event']['id'] for q in ('DEMO', 'demo', ' Demo ')}
        self.assertEqual(len(ids), 1)

    def test_bundle_matches_the_shared_spec(self):
        event_id = self.api('/api/v2/events/lookup/', q='DEMO')['event']['id']
        b = self.api(f'/api/v2/events/{event_id}/bundle/')
        ev = b['event']
        self.assertEqual(ev['event_code'], 'DEMO')
        self.assertEqual(ev['name'], 'Demo: Fort Worth Food & Music Festival')
        self.assertEqual(ev['timezone'], 'America/Chicago')
        self.assertEqual(ev['status'], 'published')
        self.assertIs(ev['paid'], True)
        self.assertIs(ev['is_demo'], False)
        self.assertEqual(ev['address'], 'Panther Island, Fort Worth, TX 76102')
        self.assertAlmostEqual(ev['latitude'], 32.7495, delta=0.001)
        self.assertAlmostEqual(ev['longitude'], -97.3316, delta=0.001)
        now = timezone.now()
        self.assertLessEqual(datetime.fromisoformat(ev['start_time']), now)
        self.assertGreaterEqual(datetime.fromisoformat(ev['end_time']), now)
        self.assertIsInstance(b['content_version'], int)
        self.assertGreaterEqual(b['content_version'], 1)

        display = b['display']
        self.assertEqual(display['resolved_preset'], 'festival')
        self.assertEqual(display['secondary_pane'], 'now_next')
        self.assertLessEqual({'map', 'locations', 'schedule', 'notifications'}, set(display['tabs']))

        locs = b['locations']
        self.assertEqual(len(locs), 18)
        self.assertEqual({l['name'] for l in locs}, LOCATION_NAMES)
        self.assertEqual({l['category'] for l in locs}, CATEGORIES)
        pinned = [l for l in locs if l['latitude'] is not None and l['longitude'] is not None]
        self.assertEqual(len(pinned), 17)
        for l in pinned:
            self.assertAlmostEqual(l['latitude'], 32.7495, delta=0.01, msg=l['name'])
            self.assertAlmostEqual(l['longitude'], -97.3316, delta=0.01, msg=l['name'])
        mariachi = next(l for l in locs if l['name'] == 'Roaming Mariachi')
        self.assertEqual(mariachi['category'], 'Stage')
        self.assertIsNone(mariachi['latitude'])
        self.assertIsNone(mariachi['longitude'])
        main = next(l for l in locs if l['name'] == 'Main Stage')
        self.assertEqual(main['category'], 'Stage')
        self.assertEqual(main['snippet'], 'Headliners every night')
        self.assertTrue(main['description'].startswith('The big one: headliners each night'))

        # API-010: tags. Lower case, 2-6 on everything; one vocabulary across the event.
        self.assertEqual(main['tags'], ['live music', 'headliners', 'free'])
        for thing in locs + b['schedule']:
            self.assertTrue(2 <= len(thing['tags']) <= 6, thing['name'])
            self.assertTrue(all(t == t.lower() for t in thing['tags']), thing['name'])
        self.assertEqual({l['name'] for l in locs if 'live music' in l['tags']},
                         {'Main Stage', 'Trinity Stage', 'Panther Island Pavilion', 'Roaming Mariachi'})
        self.assertEqual({l['name'] for l in locs if 'food' in l['tags']},
                         {'Chef Demo Kitchen', 'Taco Row', 'BBQ Pit Lane', 'Sweet Spot'})

        items = b['schedule']
        self.assertEqual(len(items), 51)
        general = [i for i in items if i['location_id'] is None]
        self.assertEqual(sorted(i['name'] for i in general), ['Gates open', 'Gates open', 'Opening ceremony'])
        self.assertTrue(all(i['tags'] == ['general', 'free'] for i in general))
        stages = {'Main Stage', 'Trinity Stage', 'Panther Island Pavilion'}
        music = [i for i in items if 'live music' in i['tags']]
        self.assertEqual(len(music), 36)
        self.assertTrue(all(i['location_name'] in stages for i in music))
        demos = [i for i in items if {'food', 'demo'} & set(i['tags'])]
        self.assertEqual(len(demos), 9)
        self.assertTrue(all(i['location_name'] == 'Chef Demo Kitchen' and {'food', 'demo'} <= set(i['tags'])
                            for i in demos))
        kids = [i for i in items if i['name'] == 'Face painting & games']
        self.assertEqual(len(kids), 3)
        self.assertTrue(all(i['location_name'] == 'Kids Zone' and {'family', 'kid-friendly'} <= set(i['tags'])
                            for i in kids))
        for i in items:
            self.assertLess(datetime.fromisoformat(i['start_time']), datetime.fromisoformat(i['end_time']), i['name'])
        self.assertTrue(any(datetime.fromisoformat(i['end_time']) > now for i in items))  # now/next not empty

        self.assertEqual({n['title'] for n in b['notifications']}, SENT_TITLES)
        self.assertEqual(len(b['notifications']), 4)
        # API-011: the set time change went out as urgent, the others as normal.
        self.assertEqual({n['title'] for n in b['notifications'] if n['priority'] == 'urgent'}, {'Set time change'})
        self.assertTrue(all(n['priority'] in ('normal', 'urgent') for n in b['notifications']))
        # Scheduled, not yet sent. On a live server the scheduler sends them when due, so the live suites accept
        # these titles too (4 to 6 notifications), never one in the future.
        unsent = Event.objects.get(pk=event_id).notification_set.filter(sent=False)
        self.assertEqual(set(unsent.values_list('title', flat=True)), {'Headliner soon', 'Day 2'})
        self.assertTrue(all(n.send_time > now for n in unsent))
