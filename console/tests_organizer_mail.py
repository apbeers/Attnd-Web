"""Stage I16: organizer emails (import done, review waiting, scheduled notification sent), each one turned off in
Account."""
from datetime import timedelta
from unittest import mock

from django.contrib.auth.models import User
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from console import organizer_mail
from events.models import Event, EventMember, Notification
from scrape.models import ChangeSet


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class OrganizerMailTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user('o', 'owner@example.com', 'pw')
        self.admin = User.objects.create_user('a', 'admin@example.com', 'pw')
        self.helper = User.objects.create_user('h', 'helper@example.com', 'pw')
        self.event = Event.objects.create(name='Fest', event_code='FESTM', user=self.owner,
                                          start_time=timezone.now() + timedelta(days=2))
        EventMember.objects.create(event=self.event, user=self.admin, role='admin')
        EventMember.objects.create(event=self.event, user=self.helper, role='contributor')

    def test_owner_and_admins_get_it_unless_they_turned_it_off(self):
        cs = ChangeSet.objects.create(event=self.event, summary='2 new sets', status=ChangeSet.PENDING)
        with mock.patch('console.mail.email_enabled', return_value=True):
            organizer_mail.review_waiting(self.event, cs, 2)
            self.assertEqual(sorted(mail.outbox[0].to), ['admin@example.com', 'owner@example.com'])
            self.client.force_login(self.admin)
            self.client.post(reverse('console:account'), {'action': 'notify', 'import_done': 'on'}, secure=True)
            organizer_mail.review_waiting(self.event, cs, 2)
            self.assertEqual(mail.outbox[1].to, ['owner@example.com'])
            self.assertIn('Turn these emails off in Account', mail.outbox[1].body)

    def test_a_scheduled_notification_going_out_is_reported(self):
        from events import push
        Notification.objects.create(event=self.event, title='Gates open', body='Come in!',
                                    send_time=timezone.now() - timedelta(minutes=1))
        with mock.patch('console.mail.email_enabled', return_value=True), \
                mock.patch('events.push.send_notification'):
            push.send_due_notifications()
        self.assertIn('Sent: “Gates open”', mail.outbox[0].subject)
