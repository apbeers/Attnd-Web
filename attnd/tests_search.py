"""F3: Postgres full-text + trigram search (attnd.search) and where it is used."""
from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone

from agent.components import events_json, matching_events
from attnd.search import ids
from events.models import Event, Location, ScheduledItem


class SearchTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('o', 'o@example.com', 'pw')
        now = timezone.now()
        self.blues = Event.objects.create(name='Riverside Blues Festival', event_code='BLUES', user=self.user,
                                          start_time=now, description='Three days of music by the river.')
        self.art = Event.objects.create(name='Thursday Art Walk', event_code='ARTWALK', user=self.user,
                                        start_time=now)
        Location.objects.create(event=self.art, name='Taco Row', category='Food')
        ScheduledItem.objects.create(event=self.art, name='Glassblowing demo', start_time=now)

    def test_stemming_typos_and_codes(self):
        qs = Event.objects.all()
        fields = {'name': 'A', 'event_code': 'A', 'description': 'C'}
        self.assertEqual(ids(qs, 'festivals', fields), [self.blues.pk])  # stemmed
        self.assertEqual(ids(qs, 'Riversde', fields), [self.blues.pk])  # typo
        self.assertEqual(ids(qs, 'artwalk', fields), [self.art.pk])
        self.assertEqual(ids(qs, 'rugby', fields), [])
        self.assertEqual(list(ids(qs, '', fields)), [])

    def test_events_are_found_by_their_places_and_items(self):
        all_ids = [self.blues.pk, self.art.pk]
        self.assertEqual(matching_events(all_ids, 'tacos'), [self.art.pk])
        self.assertEqual(matching_events(all_ids, 'glass blowing'), [self.art.pk])
        self.assertIn('Thursday Art Walk', events_json(self.user, 'taco'))
        self.assertNotIn('Riverside', events_json(self.user, 'taco'))
        self.assertIn('No event matches', events_json(self.user, 'rugby'))
