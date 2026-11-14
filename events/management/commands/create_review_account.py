"""An organizer-console login for app store reviewers, on the demo event's team.

    heroku run -a attnd-web python manage.py create_review_account
    heroku run -a attnd-web python manage.py create_review_account --email appreview@attnd.app --code DEMO

The app itself needs no login (reviewers join the event with code DEMO). This optional account lets a
reviewer sign in at /console/, edit the demo event and watch the app update live. Running it again
resets the password. The password is printed once: put it in App Store Connect (App Review
Information) and Play Console (App access), and your password manager.
"""
import secrets

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError

from console.accounts import create_account, normalize_email
from events.models import Event, EventMember


class Command(BaseCommand):
    help = 'Create (or reset) the store reviewer console account on the demo event.'

    def add_arguments(self, parser):
        parser.add_argument('--email', default='appreview@attnd.app')
        parser.add_argument('--code', default='DEMO')

    def handle(self, *args, email, code, **options):
        event = Event.objects.filter(event_code=code.upper()).first()
        if event is None:
            raise CommandError(f'No event {code.upper()}. Run: python manage.py load_demo_event --owner <your email>')
        email = normalize_email(email)
        password = '-'.join(secrets.token_urlsafe(4) for _ in range(4))
        user = User.objects.filter(email__iexact=email).first()
        if user is None:
            user = create_account(email, password)
        else:
            user.set_password(password)
            user.is_active = True
            user.save(update_fields=['password', 'is_active'])
        if user.is_superuser:
            raise CommandError(f'{email} is a site admin; use a separate reviewer address.')
        if event.user_id != user.id:
            EventMember.objects.update_or_create(event=event, user=user,
                                                 defaults={'role': EventMember.ROLE_CONTRIBUTOR})
        self.stdout.write(self.style.SUCCESS('Reviewer console account (sign in at /console/):'))
        self.stdout.write(f'  Email:    {email}')
        self.stdout.write(f'  Password: {password}')
        self.stdout.write(f'  Event:    {event.name} (code {event.event_code}), as a contributor')
