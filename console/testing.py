"""Test helpers."""
from django.test import Client

from console import twofactor


class TwoFactorClient(Client):
    """A test client whose login()/force_login() also complete the two-factor step, for tests about
    something else that sign in as staff or superusers (console/twofactor.py)."""

    def _login(self, user, backend=None):
        super()._login(user, backend)
        session = self.session
        session[twofactor.SESSION_KEY] = str(user.pk)
        session.save()
