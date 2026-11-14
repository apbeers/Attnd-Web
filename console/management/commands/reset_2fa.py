"""Turn off two-factor sign-in for an account that lost its phone, passkeys and recovery codes:

    heroku run -a attnd-web python manage.py reset_2fa someone@example.com

Their authenticator app, passkeys, emailed codes and recovery codes are removed and they are signed out
everywhere. Staff and superusers set up a passkey or authenticator the next time they sign in; other
accounts can turn them back on from their Account page.
"""
from django.contrib.auth.models import User
from django.contrib.sessions.models import Session
from django.core.management.base import BaseCommand, CommandError

from console.models import Passkey, SignInSecurity, TwoFactorDevice


class Command(BaseCommand):
    help = 'Remove an account’s second factors (authenticator, passkeys, emailed and recovery codes) and sign it out everywhere.'

    def add_arguments(self, parser):
        parser.add_argument('email')

    def handle(self, *args, email, **options):
        users = list(User.objects.filter(email__iexact=email.strip()))
        if not users:
            raise CommandError(f'No account uses {email}.')
        if len(users) > 1:
            raise CommandError(f'More than one account uses {email}; reset it in the Django admin instead.')
        user = users[0]
        deleted, _ = TwoFactorDevice.objects.filter(user=user).delete()
        keys, _ = Passkey.objects.filter(user=user).delete()
        SignInSecurity.objects.filter(user=user).update(recovery_codes=[], email_codes_enabled=False,
                                                        email_code_hash='', email_code_expires_at=None)
        signed_out = 0
        for session in Session.objects.all().iterator():
            if session.get_decoded().get('_auth_user_id') == str(user.pk):
                session.delete()
                signed_out += 1
        what = (f'Two-factor sign-in removed (authenticator app: {"yes" if deleted else "no"}, passkeys: {keys})'
                if deleted or keys else 'No authenticator app or passkey was set up')
        self.stdout.write(self.style.SUCCESS(f'{what} for {user.email}; signed out of {signed_out} session(s).'))
