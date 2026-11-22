"""Architecture corrections (stage A-fix): long lists page instead of stopping at 12, list components don't query per
row, every component is drawn by a page (or says why not), partial event forms save only their own fields."""

import json
import re
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.contrib.auth.models import User
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from console import components as C
from events.models import Event, Location, Notification, ScheduledItem


class ListTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user('owner', 'owner@example.com', 'pw')
        start = timezone.now() + timedelta(days=2)
        self.event = Event.objects.create(name='Big Fest', event_code='BIGFEST', user=self.owner, start_time=start,
                                          timezone='America/Chicago')
        self.start = start
        self.client.force_login(self.owner)

    def add(self, n):
        base = Location.objects.filter(event=self.event).count()
        for i in range(n):
            loc = Location.objects.create(event=self.event, name=f'Stage {base + i}', sort_order=base + i)
            ScheduledItem.objects.create(event=self.event, name=f'Set {base + i}', location=loc,
                                         start_time=self.start + timedelta(hours=i))
            Notification.objects.create(event=self.event, title=f'N {base + i}', body='b',
                                        send_time=self.start + timedelta(hours=i))

    def test_more_than_12_rows_render_and_page(self):
        self.add(70)
        url = reverse('agent:component_render')
        items = json.dumps([{'event_id': self.event.pk}])
        first = self.client.get(url, {'component': 'location_row', 'items': items}, secure=True).json()
        self.assertEqual(len(first['items']), 60)
        self.assertEqual(first['total'], 70)
        self.assertEqual(first['next_offset'], 60)
        rest = self.client.get(url, {'component': 'location_row', 'items': items, 'offset': 60}, secure=True).json()
        self.assertEqual(len(rest['items']), 10)
        self.assertIsNone(rest['next_offset'])
        page = self.client.get(reverse('console:assistant') + f'?event={self.event.pk}', secure=True)
        self.assertContains(page, 'data-more-offset="60"')

    def test_list_components_load_without_a_query_per_row(self):
        counts = {}
        for n in (3, 15):
            self.add(n if not counts else n - 3)
            for name in ('location_row', 'schedule_item_row', 'notification_row', 'event_tile'):
                with CaptureQueriesContext(connection) as ctx:
                    found = C.instances(self.owner, name, [{'event_id': self.event.pk}] if name != 'event_tile' else [{}])
                counts.setdefault(name, []).append(len(ctx.captured_queries))
                self.assertTrue(found)
        for name, (few, many) in counts.items():
            self.assertEqual(few, many, f'{name}: {few} queries for 3 rows, {many} for 15')


class DrawnOnceTests(TestCase):
    def test_every_component_is_drawn_by_a_page_or_says_why_not(self):
        used = set()
        for root in [Path(settings.BASE_DIR)]:
            for path in root.glob('*/templates/**/*.html'):
                if '/components/' in str(path):
                    continue
                used |= set(re.findall(r'\{% component "([a-z_]+)"', path.read_text()))
        for name, comp in C.registry().items():
            parts_used = any(name in C.get(p).parts for p in used if p in C.registry())
            if name in used or parts_used:
                continue
            self.assertTrue(comp.chat_only, f'{name} is drawn by no page: use it on its page, or set chat_only=<why>')


class PartialEventFormTests(TestCase):
    def test_the_context_card_saves_only_the_context(self):
        owner = User.objects.create_user('o2', 'o2@example.com', 'pw')
        event = Event.objects.create(name='Fest', event_code='FEST2', user=owner, start_time=timezone.now(),
                                     description='Kept')
        self.client.force_login(owner)
        Event.objects.filter(pk=event.pk).update(description='Changed meanwhile')
        response = self.client.post(reverse('console:event_edit', args=[event.pk]),
                                    {'part': 'context', 'ctx_about': 'A great day out.'}, secure=True)
        self.assertEqual(response.status_code, 302)
        event.refresh_from_db()
        self.assertEqual(event.description, 'Changed meanwhile')  # not overwritten by a stale copy
        self.assertEqual(event.context.get('about'), 'A great day out.')
        data = C.load(owner, 'event_context_form', {'event_id': event.pk})
        self.assertEqual(data['form']['hidden'], {'part': 'context'})
        self.assertNotIn('name', data['form']['fields'])
