"""Your events in three sections (batch 6 §7): the dashboard, the canvas Events tab, the chat and list_events use the
same rules; showcase events and DEMO in a folded staff-only section; no Demo badge, Paid on paid events."""
from datetime import timedelta

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from console import components as C
from console.components.catalog import event_section, sectioned, status_badges
from console.testing import TwoFactorClient
from events.models import Event


@override_settings(DEMO_EVENT_CODE='DEMO')
class EventSectionsTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user('o', 'o@example.com', 'pw')
        now = timezone.now()

        def ev(code, start, hours=4, **kw):
            return Event.objects.create(name=code.title(), event_code=code, user=self.owner, timezone='UTC',
                                        start_time=now + start, end_time=now + start + timedelta(hours=hours), **kw)
        self.live = ev('LIVE1', timedelta(hours=-1))
        self.soon = ev('SOON1', timedelta(days=2))
        self.later = ev('LATER1', timedelta(days=20), status=Event.STATUS_DRAFT)
        self.pasts = [ev(f'PAST{i}', timedelta(days=-30 - i)) for i in range(8)]
        self.demo = ev('DEMO', timedelta(hours=-1), paid=True)
        self.show = ev('SHOWX', timedelta(hours=-1), showcase=True)

    def test_sections_and_order(self):
        parts = dict(sectioned(list(Event.objects.all()), self.owner))
        self.assertEqual([e.event_code for e in parts['now']][:1], ['LIVE1'])
        self.assertIn('DEMO', [e.event_code for e in parts['now']])  # organizers: DEMO by its dates
        self.assertEqual([e.event_code for e in parts['upcoming']], ['SOON1', 'LATER1'])  # soonest first, drafts too
        self.assertEqual([e.event_code for e in parts['past']][:2], ['PAST0', 'PAST1'])  # most recent first
        self.assertNotIn('showcase', parts)
        staff = User.objects.create_user('s', 's@example.com', 'pw', is_staff=True)
        staff_parts = dict(sectioned(list(Event.objects.all()), staff))
        self.assertEqual(sorted(e.event_code for e in staff_parts['showcase']), ['DEMO', 'SHOWX'])
        self.assertEqual(event_section(self.live, staff), 'now')

    def test_badges(self):
        self.assertNotIn('Demo', [b['label'] for b in status_badges(self.live)])
        self.assertIn('Paid', [b['label'] for b in status_badges(self.demo)])

    def test_dashboard_canvas_and_agent(self):
        self.client.force_login(self.owner)
        page = self.client.get(reverse('console:dashboard'), secure=True).content.decode()
        self.assertIn('Happening now · ', page)
        self.assertIn('Upcoming · 2', page)
        self.assertIn('Past · 8', page)
        self.assertIn('Show all past events (8)', page)
        self.assertLess(page.index('Happening now'), page.index('Upcoming ·'))
        # The canvas Events tab: the same groups, Past folded after 6.
        canvas = self.client.get(reverse('console:assistant'), secure=True).content.decode()
        self.assertIn('data-group-toggle="now"', canvas)
        self.assertIn('Show all past events (8)', canvas)
        self.assertEqual(canvas.count('data-more'), 2)
        # Each section is its own block: its heading, then its own cards (not headings and cards side by side in one
        # grid); every section starts open (owner, Nov 21, 2026).
        import re
        blocks = re.findall(r'<section class="agent__groupsec" data-group-section="(\w+)"([^>]*)>(.*?)</section>', canvas, re.S)
        self.assertEqual([b[0] for b in blocks], ['now', 'upcoming', 'past'])
        for group, attrs, inner in blocks:
            self.assertNotIn('data-folded', attrs, group)
            self.assertIn(f'data-group-toggle="{group}" aria-expanded="true"', inner)
            self.assertIn('class="agent__card"', inner, group)
        self.assertEqual(re.findall(r'data-group-section="past".*?</section>', canvas, re.S)[0].count('class="agent__card"'), 8)
        # The agent's list: every event carries its section.
        from agent.components import events_json
        text = events_json(self.owner)
        self.assertIn('"section":"Happening now"', text)
        self.assertIn('"section":"Past"', text)
        data = C.load(self.owner, 'event_tile', {'event_id': self.soon.pk})
        self.assertEqual((data['group'], data['section']), ('upcoming', 'Upcoming'))

    def test_staff_showcase_section_open(self):
        staff = User.objects.create_user('s', 's@example.com', 'pw', is_staff=True)
        self.show.user = staff
        self.show.save()
        client = TwoFactorClient()
        client.force_login(staff)
        page = client.get(reverse('console:dashboard') + '?page=1', secure=True).content.decode()
        self.assertIn('<h2 class="eyebrow">Showcase &amp; demo · 1</h2>', page)  # open like every section
