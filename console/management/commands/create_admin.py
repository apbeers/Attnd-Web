"""Create a site admin who signs in with their email:

    heroku run -a attnd-dev python manage.py create_admin you@example.com

Prompts for the password. The internal username is generated (people only ever see their email).
"""
import getpass

from django.core.management.base import BaseCommand, CommandError

from console.accounts import create_account, email_in_use, normalize_email


class Command(BaseCommand):
    help = 'Create a site admin account (sign in with the email).'

    def add_arguments(self, parser):
        parser.add_argument('email')

    def handle(self, *args, email, **options):
        email = normalize_email(email)
        if '@' not in email:
            raise CommandError('That is not an email address.')
        if email_in_use(email):
            raise CommandError(f'An account already uses {email}. To make it an admin, use the Django admin '
                               'or: python manage.py shell -c "from django.contrib.auth.models import User; '
                               f"User.objects.filter(email__iexact='{email}').update(is_superuser=True, is_staff=True)\"")
        password = getpass.getpass('Password: ')
        if len(password) < 8 or password != getpass.getpass('Password (again): '):
            raise CommandError('Passwords must match and be at least 8 characters.')
        create_account(email, password, is_superuser=True, is_staff=True)
        self.stdout.write(self.style.SUCCESS(f'Admin {email} created. Sign in at /console/ with that email.'))
