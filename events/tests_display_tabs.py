"""At most five tabs (iOS shows five, Home counts as one; owner, Nov 21, 2026)."""
from django.test import SimpleTestCase

from console.forms.event_forms import DisplayForm
from events.display import TAB_LIMIT_NOTE, fit_tabs


class TabLimitTests(SimpleTestCase):
    def test_fit_tabs_drops_flyer_then_duplicates_never_home_or_updates(self):
        self.assertEqual(fit_tabs(['home', 'map', 'locations', 'schedule', 'flyer', 'notifications']),
                         (['home', 'map', 'locations', 'schedule', 'notifications'], ['flyer']))
        kept, dropped = fit_tabs(['home', 'map', 'locations', 'schedule', 'notifications', 'flyer', 'x'])
        self.assertEqual(dropped, ['flyer', 'locations'])
        self.assertIn('home', kept)
        self.assertIn('notifications', kept)
        self.assertEqual(fit_tabs(['map', 'schedule']), (['map', 'schedule'], []))

    def test_the_form_refuses_six(self):
        # Home is always on and counts: five more is six.
        data = {'preset': 'auto', 'customize_tabs': 'on', 'tabs': ['map', 'locations', 'schedule', 'flyer',
                                                                    'notifications']}
        form = DisplayForm(data)
        self.assertFalse(form.is_valid())
        self.assertEqual(form.errors['tabs'], [TAB_LIMIT_NOTE])
        data['tabs'] = data['tabs'][:4]
        self.assertTrue(DisplayForm(data).is_valid())
        self.assertNotIn('home', DisplayForm(data).fields['tabs'].choices)


class TabLimitNoteTests(__import__('django.test', fromlist=['TestCase']).TestCase):
    def test_old_six_tab_list_is_cut_and_the_console_says_so(self):
        from datetime import timedelta

        from django.contrib.auth.models import User
        from django.utils import timezone

        from console import components as C
        from events.display import event_stats, resolve_display
        from events.models import Event
        user = User.objects.create_user('o', 'o@example.com', 'pw')
        event = Event.objects.create(name='Fair', event_code='FAIR6', user=user, timezone='UTC',
                                     start_time=timezone.now() + timedelta(days=1),
                                     display_config={'tabs': ['home', 'map', 'locations', 'schedule', 'flyer',
                                                              'notifications']})
        tabs = resolve_display(event, event_stats(event), sections=2)['tabs']
        self.assertEqual(tabs, ['home', 'map', 'locations', 'schedule', 'notifications'])
        html = C.render('app_layout', C.load(user, 'app_layout', {'event_id': event.pk}))
        self.assertIn('Not shown in the apps: Flyer (5-tab limit)', html)
