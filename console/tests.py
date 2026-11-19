from django.contrib.auth.models import User
from django.test import TestCase, Client, override_settings
from django.urls import reverse
from django.utils import timezone
import datetime

from console.testing import TwoFactorClient
from events.models import Device, Event, EventInvite, EventMember, Location, Notification

TRIAL_USERNAME = "trialuser"


def _make_trial_user():
    user, _ = User.objects.get_or_create(username=TRIAL_USERNAME, defaults={"email": "trial@attnd.app"})
    user.set_unusable_password()
    user.save()
    return user


@override_settings(SIGNUPS_OPEN=True)
class SignUpViewTests(TestCase):

    def setUp(self):
        self.client = Client()
        self.url = reverse('console:signup')
        self.valid_data = {
            'email': 'new@example.com',
            'password': 'Str0ng!Pass99',
        }

    def _post(self, data):
        return self.client.post(self.url, data, secure=True)

    def test_get_returns_200(self):
        response = self.client.get(self.url, secure=True)
        self.assertEqual(response.status_code, 200)

    def test_valid_post_redirects_to_console(self):
        response = self._post(self.valid_data)
        self.assertRedirects(response, reverse('console:dashboard'), fetch_redirect_response=False)

    def test_valid_post_creates_regular_user(self):
        self._post(self.valid_data)
        user = User.objects.get(email='new@example.com')
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)
        self.assertEqual(user.email, 'new@example.com')

    def test_claim_transfers_trial_event(self):
        """Signing up with ?claim=CODE transfers the trial event to the new user."""
        trial_user = _make_trial_user()
        event = Event.objects.create(
            name="Scraped Festival",
            event_code="TESTCODE",
            description="desc",
            latitude=0, longitude=0,
            start_time=timezone.now() + datetime.timedelta(days=7),
            address="123 Main St",
            user=trial_user,
        )
        Location.objects.create(
            name="Main Stage", snippet="s", description="d",
            latitude=0, longitude=0, address="", user=trial_user, event=event,
        )
        Notification.objects.create(
            title="Welcome!", body="Thanks for joining.",
            send_time=timezone.now() + datetime.timedelta(hours=1),
            user=trial_user, event=event,
        )

        data = {**self.valid_data, 'claim': 'TESTCODE'}
        self._post(data)

        new_user = User.objects.get(email='new@example.com')
        event.refresh_from_db()
        self.assertEqual(event.user, new_user)
        self.assertEqual(event.location_set.filter(user=new_user).count(), 1)
        self.assertEqual(event.notification_set.filter(user=new_user).count(), 1)
        # No extra demo event should be created
        self.assertEqual(Event.objects.filter(user=new_user).count(), 1)

    def test_claim_bad_code_lands_on_dashboard(self):
        """An invalid claim code still creates the account and lands on the empty dashboard."""
        data = {**self.valid_data, 'claim': 'BADCODE99'}
        response = self._post(data)
        user = User.objects.get(email='new@example.com')
        self.assertEqual(Event.objects.filter(user=user).count(), 0)
        self.assertRedirects(response, reverse('console:dashboard'), fetch_redirect_response=False)

    def test_duplicate_email_returns_200_with_form_error(self):
        User.objects.create_user(username='new@example.com', email='new@example.com', password='x')
        response = self._post(self.valid_data)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(User.objects.filter(username='new@example.com').count(), 1)
        self.assertFormError(response.context['form'], 'email', 'An account with this email already exists.')

    def test_invalid_email_returns_200_with_form_errors(self):
        response = self._post({'email': 'not-an-email', 'password': 'Str0ng!Pass99'})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(email='not-an-email').exists())

    def test_missing_password_returns_200_with_form_errors(self):
        response = self._post({'email': 'new@example.com', 'password': ''})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(email='new@example.com').exists())


# ---------------------------------------------------------------------------
# Organizer console
# ---------------------------------------------------------------------------

from unittest.mock import patch  # noqa: E402

from events.models import EventMember, ScheduledItem  # noqa: E402
from scrape import diff  # noqa: E402
from scrape.models import ChangeSet, IngestJob  # noqa: E402


class ConsoleTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user('owner@example.com', 'owner@example.com', 'pw')
        self.other = User.objects.create_user('other@example.com', 'other@example.com', 'pw')
        self.event = Event.objects.create(
            name='Art Walk', event_code='ARTWALK1', start_time=timezone.now() + datetime.timedelta(days=2),
            timezone='America/Chicago', latitude=32.75, longitude=-97.33, user=self.owner,
            status=Event.STATUS_DRAFT)
        self.loc = Location.objects.create(event=self.event, user=self.owner, name='Gallery A',
                                           latitude=32.751, longitude=-97.331, category='Gallery')
        self.item = ScheduledItem.objects.create(event=self.event, user=self.owner, location=self.loc,
                                                 name='Opening', start_time=self.event.start_time)
        self.client = TwoFactorClient()  # some tests sign in as a superuser
        self.client.force_login(self.owner)

    def url(self, name, *args):
        return reverse(f'console:{name}', args=[self.event.pk, *args])

    def test_every_page_renders(self):
        pages = [reverse('console:dashboard'), reverse('console:event_new'),
                 self.url('event'), self.url('event_edit'), self.url('locations'), self.url('location_new'),
                 self.url('location_edit', self.loc.pk), self.url('schedule'), self.url('item_new'),
                 self.url('item_edit', self.item.pk), self.url('notifications'), self.url('display'),
                 self.url('changesets'), self.url('history'), self.url('team'), self.url('event_poster'),
                 self.url('event_qr')]
        for page in pages:
            with self.subTest(page=page):
                self.assertEqual(self.client.get(page, secure=True).status_code, 200)

    def test_other_users_cannot_see_event(self):
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(self.url('event'), secure=True).status_code, 404)

    def test_team_member_can_edit(self):
        EventMember.objects.create(event=self.event, user=self.other)
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(self.url('locations'), secure=True).status_code, 200)

    def test_anonymous_redirects_to_login(self):
        self.client.logout()
        resp = self.client.get(reverse('console:dashboard'), secure=True)
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/accounts/login/', resp['Location'])

    def test_manual_edit_locks_fields(self):
        resp = self.client.post(self.url('location_edit', self.loc.pk), {
            'name': 'Gallery A', 'category': 'Gallery', 'snippet': '', 'description': 'Hand written',
            'address': '', 'latitude': '32.751', 'longitude': '-97.331', 'website': '', 'image_url': '',
        }, secure=True)
        self.assertEqual(resp.status_code, 302)
        self.loc.refresh_from_db()
        self.assertEqual(self.loc.description, 'Hand written')
        self.assertEqual(self.loc.locked_fields, ['description'])

    def test_item_times_are_entered_in_event_timezone(self):
        resp = self.client.post(self.url('item_new'), {
            'name': 'Talk', 'start_time': '2026-07-04T18:00', 'end_time': '', 'location': self.loc.pk,
            'description': '', 'tags_text': 'Talks, Kids',
        }, secure=True)
        self.assertEqual(resp.status_code, 302)
        item = ScheduledItem.objects.get(name='Talk')
        self.assertEqual(item.start_time.astimezone(datetime.timezone.utc).hour, 23)  # 18:00 CDT
        self.assertEqual(item.tags, ['talks', 'kids'])  # API-010: normalised to lower case

    def test_publish_and_list(self):
        self.client.post(self.url('event_status'), {'action': 'publish'}, secure=True)
        self.client.post(self.url('event_status'), {'action': 'list'}, secure=True)
        self.event.refresh_from_db()
        self.assertEqual(self.event.status, Event.STATUS_PUBLISHED)
        self.assertTrue(self.event.public)

    def test_display_settings_saved(self):
        data = {'preset': 'festival', 'customize_tabs': 'on', 'tabs': ['schedule', 'map'],
                'tab_order': 'schedule,map,locations,flyer,notifications', 'default_tab': '',
                'locations_style': 'cards', 'schedule_style': '', 'secondary_pane': '',
                'map_clustering': '0', 'map_fit_all': '', 'show_location_images': '', 'enable_search': '',
                'group_by_category': '', 'accent_color': '#123456'}
        self.client.post(self.url('display'), data, secure=True)
        self.event.refresh_from_db()
        self.assertEqual(self.event.display_config, {
            'preset': 'festival', 'tabs': ['schedule', 'map'], 'locations_style': 'cards', 'map_clustering': False})
        self.assertEqual(self.event.accent_color, '#123456')

    def test_reorder_locations(self):
        loc2 = Location.objects.create(event=self.event, user=self.owner, name='Gallery B')
        self.client.post(self.url('locations_reorder'), {'order': f'{loc2.pk},{self.loc.pk}'}, secure=True)
        self.assertEqual(list(self.event.location_set.order_by('sort_order').values_list('name', flat=True)),
                         ['Gallery B', 'Gallery A'])

    def test_ai_create_queues_job(self):
        with patch('scrape.jobs._start_background'):
            with self.captureOnCommitCallbacks(execute=True):
                resp = self.client.post(reverse('console:event_new'), {
                    'mode': 'ai', 'ai-urls': 'https://example.com', 'ai-text': '', 'ai-instructions': ''},
                    secure=True)
        job = IngestJob.objects.get()
        self.assertRedirects(resp, reverse('console:job', args=[job.pk]), fetch_redirect_response=False)
        self.assertEqual(job.created_by, self.owner)
        self.assertEqual(job.urls, ['https://example.com'])

    def test_review_and_apply_changes(self):
        cs = diff.save_changeset(self.event, None, [
            {'entity': 'location', 'op': 'update', 'target_id': self.loc.pk, 'label': 'Gallery A',
             'data': {'description': {'old': '', 'new': 'New text'}, 'snippet': {'old': '', 'new': 'Short'}},
             'conflict_fields': [], 'minor': True},
            {'entity': 'item', 'op': 'delete', 'target_id': self.item.pk, 'label': 'Opening', 'data': {},
             'reason': 'Cancelled', 'conflict_fields': [], 'minor': False},
        ])
        page = self.client.get(reverse('console:changeset', args=[cs.pk]), secure=True)
        self.assertContains(page, 'New text')
        update, delete = cs.changes.order_by('sort')
        self.client.post(reverse('console:changeset', args=[cs.pk]), {
            'action': 'apply', f'accept_{update.pk}': '1', f'field_{update.pk}_snippet': '1'}, secure=True)
        self.loc.refresh_from_db()
        self.assertEqual(self.loc.snippet, 'Short')
        self.assertEqual(self.loc.description, '')
        self.assertTrue(ScheduledItem.objects.filter(pk=self.item.pk).exists())  # delete not ticked
        cs.refresh_from_db()
        self.assertEqual(cs.status, ChangeSet.APPLIED)

    def test_history_restore(self):
        self.client.post(self.url('history'), {'action': 'snapshot'}, secure=True)
        self.loc.delete()
        rev = self.event.revisions.first()
        self.client.post(self.url('history'), {'revision': rev.pk}, secure=True)
        self.assertTrue(self.event.location_set.filter(name='Gallery A').exists())

    @patch('events.push.deliver')
    def test_send_notification_now(self, deliver):
        from events.push import SendResult
        deliver.return_value = SendResult(delivered=3)
        self.event.status = Event.STATUS_PUBLISHED
        self.event.save()
        self.client.post(self.url('notifications'), {'title': 'Hi', 'body': 'Doors open', 'when': 'now'}, secure=True)
        n = Notification.objects.get()
        self.assertTrue(n.sent)
        self.assertEqual(n.delivered_count, 3)


class NotificationSlotTests(ConsoleTests):
    """Scheduled notifications must land on the 10-minute marks Heroku Scheduler runs at."""

    def _schedule(self, when):
        return self.client.post(self.url('notifications'), {
            'title': 'Doors', 'body': 'Open soon', 'when': 'later', 'send_time': when}, secure=True)

    def test_off_slot_time_is_rejected(self):
        resp = self._schedule('2030-07-04T18:05')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, '10-minute mark')
        self.assertFalse(Notification.objects.exists())

    def test_on_slot_time_is_saved_in_event_timezone(self):
        resp = self._schedule('2030-07-04T18:10')
        self.assertEqual(resp.status_code, 302)
        n = Notification.objects.get()
        self.assertEqual(n.send_time.astimezone(datetime.timezone.utc).strftime('%H:%M'), '23:10')

    def test_past_time_is_rejected(self):
        self.assertEqual(self._schedule('2020-01-01T10:00').status_code, 200)
        self.assertFalse(Notification.objects.exists())

    def test_picker_only_offers_slots_and_default_is_a_slot(self):
        page = self.client.get(self.url('notifications'), secure=True)
        self.assertContains(page, 'step="600"')
        initial = page.context['form'].initial['send_time']
        self.assertEqual((initial.minute % 10, initial.second), (0, 0))

    def test_model_validation_applies_to_admin_too(self):
        from django.core.exceptions import ValidationError
        n = Notification(event=self.event, user=self.owner, title='t', body='b',
                         send_time=timezone.now().replace(minute=7, second=0, microsecond=0))
        with self.assertRaises(ValidationError):
            n.full_clean()

    def test_next_send_slot(self):
        from events.models import next_send_slot
        base = timezone.now().replace(hour=10, minute=0, second=0, microsecond=0)
        self.assertEqual(next_send_slot(base), base)
        self.assertEqual(next_send_slot(base + datetime.timedelta(minutes=1)).minute, 10)
        self.assertEqual(next_send_slot(base + datetime.timedelta(minutes=59, seconds=1)).hour, 11)


class UsagePageTests(ConsoleTests):
    def setUp(self):
        super().setUp()
        from events import usage
        start = timezone.now() - datetime.timedelta(hours=5)
        for h in range(5):
            at = start + datetime.timedelta(hours=h)
            usage.record(self.event, 'app_open', platform='ios', count=h + 1, at=at)
            usage.record(self.event, 'tab_view', detail='map', platform='android', count=2, at=at)
        usage.record(self.event, 'location_view', detail=str(self.loc.id), platform='ios', count=7)
        usage.record(self.event, 'schedule_item_view', detail=str(self.item.id), platform='ios', count=3)
        usage.record(self.event, 'join', platform='ios', count=4)
        usage.record(None, 'install', platform='ios', count=4)

    def test_owner_sees_event_usage(self):
        html = self.client.get(self.url('usage')).content.decode()
        self.assertIn('Gallery A', html)
        self.assertIn('Opening', html)
        self.assertIn('Map tab', html)
        self.assertIn('<svg', html)
        self.assertIn('>15<', html)  # app opens 1+2+3+4+5

    def test_other_users_cannot_see_it(self):
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(self.url('usage')).status_code, 404)
        self.assertEqual(self.client.get(reverse('console:admin_usage')).status_code, 404)

    def test_admin_sees_app_usage_and_support_lookup(self):
        admin = User.objects.create_superuser('admin@example.com', 'admin@example.com', 'pw')
        self.client.force_login(admin)
        Device.objects.create(id='3F2A9C1B-1111-2222-3333-444455556666', platform='ios', app_version='2.0.90',
                              api_version=2, event=self.event, last_seen=timezone.now())
        html = self.client.get(reverse('console:admin_usage')).content.decode()
        self.assertIn('Art Walk', html)
        self.assertIn('2.0.90', html)
        found = self.client.get(reverse('console:admin_usage'), {'code': '3f2a9c1b'}).content.decode()
        self.assertIn('3F2A9C1B-1111-2222-3333-444455556666', found)
        self.assertIn('ARTWALK1', found)
        missing = self.client.get(reverse('console:admin_usage'), {'code': 'ABC'}).content.decode()
        self.assertIn('No device matches', missing)
        # Admins can open any event's usage page.
        self.assertEqual(self.client.get(self.url('usage')).status_code, 200)


@override_settings(SIGNUPS_OPEN=True)
class AccountEmailTests(TestCase):
    def test_signup_rejects_an_email_already_used_by_any_account(self):
        User.objects.create_superuser('u8741', 'Owner@Example.com', 'pw')
        response = self.client.post(reverse('console:signup'), {
            'email': 'owner@example.COM', 'password': 'a-strong-password-123'})
        self.assertContains(response, 'An account with this email already exists.')
        self.assertEqual(User.objects.filter(email__iexact='owner@example.com').count(), 1)

    def test_new_accounts_store_lowercase_email(self):
        self.client.post(reverse('console:signup'), {'email': 'New@Example.com', 'password': 'a-strong-password-123'})
        user = User.objects.get(email='new@example.com')
        self.assertNotIn('@', user.username)  # the username is an internal id, never the email
        self.assertTrue(self.client.login(username='NEW@example.com', password='a-strong-password-123'))

    def test_sign_in_with_email_or_username_any_case(self):
        User.objects.create_superuser('u8741', 'admin@example.com', 'pw')
        for name in ('admin@example.com', 'ADMIN@example.com', 'u8741', 'U8741'):
            self.assertTrue(self.client.login(username=name, password='pw'), name)
            self.client.logout()
        self.assertFalse(self.client.login(username='admin@example.com', password='wrong'))
        self.assertFalse(self.client.login(username='nobody@example.com', password='pw'))


class TeamAndOwnershipTests(ConsoleTests):
    def post_team(self, **data):
        return self.client.post(self.url('team'), data, follow=True)

    def test_deleting_the_owner_leaves_the_event_unassigned(self):
        self.owner.delete()
        self.event.refresh_from_db()
        self.assertIsNone(self.event.user)
        self.assertTrue(Location.objects.filter(pk=self.loc.pk).exists())
        admin = User.objects.create_superuser('root', 'root@example.com', 'pw')
        self.client.force_login(admin)
        self.assertContains(self.client.get(reverse('console:admin_usage')), 'Unassigned events')
        self.assertContains(self.client.get(self.url('team')), 'This event is unassigned')
        self.post_team(action='assign_owner', email='OTHER@example.com')
        self.event.refresh_from_db()
        self.assertEqual(self.event.user, self.other)

    def test_invite_before_signup_becomes_membership(self):
        self.post_team(action='invite', email='Helper@Example.com', role='admin')
        self.assertTrue(EventInvite.objects.filter(event=self.event, email='helper@example.com', role='admin').exists())
        self.client.logout()
        self.client.post(reverse('console:signup'), {'email': 'helper@example.com', 'password': 'a-strong-password-123'})
        helper = User.objects.get(email='helper@example.com')
        self.assertEqual(EventMember.objects.get(event=self.event, user=helper).role, 'admin')
        self.assertFalse(EventInvite.objects.filter(event=self.event).exists())
        self.assertEqual(self.client.get(self.url('locations')).status_code, 200)

    def test_contributors_edit_but_cannot_manage_the_team(self):
        EventMember.objects.create(event=self.event, user=self.other, role='contributor')
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(self.url('schedule')).status_code, 200)
        self.post_team(action='invite', email='x@example.com', role='admin')
        self.assertFalse(EventInvite.objects.exists())
        self.client.post(self.url('event_status'), {'action': 'unpublish'})
        self.client.post(self.url('event_delete'), {'confirm_code': self.event.event_code})
        self.assertTrue(Event.objects.filter(pk=self.event.pk).exists())

    def test_admins_add_contributors_only_and_owner_can_transfer(self):
        EventMember.objects.create(event=self.event, user=self.other, role='admin')
        self.client.force_login(self.other)
        self.post_team(action='invite', email='new@example.com', role='admin')
        self.assertEqual(EventInvite.objects.get(email='new@example.com').role, 'contributor')
        self.client.force_login(self.owner)
        member = EventMember.objects.get(event=self.event, user=self.other)
        self.post_team(action='transfer', member=member.pk)
        self.event.refresh_from_db()
        self.assertEqual(self.event.user, self.other)
        self.assertEqual(EventMember.objects.get(event=self.event, user=self.owner).role, 'admin')


class AccountPageTests(TestCase):
    def setUp(self):
        from console.accounts import create_account
        self.user = create_account('Me@Example.com', 'old-password-123')
        self.client.force_login(self.user)

    def test_change_email_keeps_the_account(self):
        User.objects.create_user('someone', 'taken@example.com', 'x')
        r = self.client.post(reverse('console:account'), {'action': 'email', 'email-email': 'TAKEN@example.com',
                                                           'email-password': 'old-password-123'})
        self.assertContains(r, 'Another account already uses that email.')
        self.client.post(reverse('console:account'), {'action': 'email', 'email-email': 'New@Example.com',
                                                       'email-password': 'old-password-123'})
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, 'new@example.com')
        self.client.logout()
        self.assertTrue(self.client.login(username='new@example.com', password='old-password-123'))
        self.assertFalse(self.client.login(username='me@example.com', password='old-password-123'))

    def test_wrong_password_does_not_change_email(self):
        self.client.post(reverse('console:account'), {'action': 'email', 'email-email': 'x@example.com',
                                                       'email-password': 'nope'})
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, 'me@example.com')

    def test_create_admin_command(self):
        from unittest.mock import patch

        from django.core.management import call_command
        with patch('getpass.getpass', return_value='a-strong-password-123'):
            call_command('create_admin', 'Boss@Example.com')
        boss = User.objects.get(email='boss@example.com')
        self.assertTrue(boss.is_superuser)
        self.assertNotIn('@', boss.username)


class BackgroundTaskTests(ConsoleTests):
    def test_refresh_in_the_background_returns_a_task_and_the_feed_lists_it(self):
        from unittest.mock import patch
        self.event.source_urls = ['https://example.com/']
        self.event.save()
        with patch('scrape.jobs._start_background'):
            r = self.client.post(self.url('event_refresh'), {}, HTTP_ACCEPT='application/json')
        self.assertEqual(r.status_code, 202)
        task = r.json()['task']
        self.assertEqual(task['status'], 'queued')
        self.assertIn('Checking Art Walk', task['title'])
        feed = self.client.get(reverse('console:tasks')).json()
        self.assertEqual(feed['active'], 1)
        self.assertEqual(feed['tasks'][0]['id'], task['id'])
        # Other people don't see it.
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(reverse('console:tasks')).json()['tasks'], [])

    def test_errors_come_back_as_json(self):
        r = self.client.post(self.url('event_refresh'), {}, HTTP_ACCEPT='application/json')
        self.assertEqual(r.status_code, 400)
        self.assertIn('source website', r.json()['error'])

    def test_service_worker_and_manifest(self):
        sw = self.client.get(reverse('console:service_worker'))
        self.assertEqual(sw['Service-Worker-Allowed'], '/console/')
        self.assertIn('application/javascript', sw['Content-Type'])
        self.assertEqual(self.client.get(reverse('console:manifest')).json()['theme_color'], '#431f86')


class LiveEditingTests(ConsoleTests):
    def test_layout_autosaves_and_bumps_the_version_apps_poll(self):
        before = self.event.content_version
        r = self.client.post(self.url('display'), {'preset': 'festival', 'locations_style': 'list'},
                             HTTP_ACCEPT='application/json')
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()['saved'])
        self.event.refresh_from_db()
        self.assertGreater(self.event.content_version, before)
        self.assertContains(self.client.get(self.url('display')), 'Live on your phone')


# ---------------------------------------------------------------------------
# Email (SETUP.md step 11)
# ---------------------------------------------------------------------------
from django.conf import settings as django_settings  # noqa: E402
from django.core import mail  # noqa: E402
from django.test import override_settings  # noqa: E402

from console.mail import AllowListSMTPBackend, allowed  # noqa: E402


class EmailTests(ConsoleTests):
    def test_smtp_uses_tls(self):
        self.assertTrue(django_settings.EMAIL_SMTP_OPTIONS['use_tls'])

    @override_settings(EMAIL_ENABLED=True, SITE_URL='https://dev.example.com',
                       DEFAULT_FROM_EMAIL='Attnd <noreply@mg.example.com>')
    def test_invite_email_links_to_this_site_from_the_default_sender(self):
        self.client.post(self.url('team'), {'action': 'invite', 'email': 'helper@example.com', 'role': 'admin'})
        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertEqual(message.from_email, 'Attnd <noreply@mg.example.com>')
        self.assertEqual(message.to, ['helper@example.com'])
        self.assertIn('https://dev.example.com/console/signup/', message.body)
        self.assertNotIn('https://attnd.app', message.body)

    def test_no_invite_email_without_credentials(self):
        with override_settings(EMAIL_ENABLED=False):
            self.client.post(self.url('team'), {'action': 'invite', 'email': 'helper@example.com', 'role': 'admin'})
        self.assertEqual(mail.outbox, [])

    def test_password_reset_flow(self):
        self.client.logout()
        self.assertContains(self.client.get(reverse('login')), 'Forgot your password?')
        r = self.client.post(reverse('password_reset'), {'email': self.owner.email.upper()}, follow=True)
        self.assertContains(r, 'Check your email')
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].subject, 'Reset your Attnd password')
        link = next(line for line in mail.outbox[0].body.splitlines() if '/accounts/reset/' in line)
        r = self.client.get(link.replace('http://testserver', ''), follow=True)
        self.assertContains(r, 'Choose a new password')
        r = self.client.post(r.redirect_chain[-1][0], {'new_password1': 'a-new-strong-pass-42',
                                                       'new_password2': 'a-new-strong-pass-42'}, follow=True)
        self.assertContains(r, 'Your new password is set')
        self.assertTrue(self.client.login(username=self.owner.email, password='a-new-strong-pass-42'))

    def test_password_reset_for_unknown_email_sends_nothing(self):
        self.client.logout()
        self.client.post(reverse('password_reset'), {'email': 'nobody@example.com'})
        self.assertEqual(mail.outbox, [])

    def test_allow_list(self):
        self.assertTrue(allowed('Me <me@example.com>', ['me@example.com']))
        self.assertTrue(allowed('anyone@team.example', ['@team.example']))
        self.assertFalse(allowed('else@example.com', ['me@example.com', '@team.example']))

    @override_settings(EMAIL_ALLOWED_RECIPIENTS=['me@example.com'])
    def test_allow_list_backend_drops_other_recipients(self):
        sent = []
        with patch('django.core.mail.backends.smtp.EmailBackend.send_messages',
                   lambda self, messages: sent.extend(messages) or len(messages)):
            backend = AllowListSMTPBackend(alias='default', host='smtp.example.com', port=587)
            a = mail.EmailMessage('one', 'x', to=['me@example.com', 'other@example.com'])
            b = mail.EmailMessage('two', 'x', to=['other@example.com'])
            self.assertEqual(backend.send_messages([a, b]), 1)
        self.assertEqual([m.subject for m in sent], ['one'])
        self.assertEqual(sent[0].to, ['me@example.com'])
