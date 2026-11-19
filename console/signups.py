"""Who may create an organizer account (SIGNUPS_OPEN).

Attnd is invite-only for now: with `SIGNUPS_OPEN` off (the default) only invited emails can sign up (a pending team
invite; Studio access and STUDIO_BETA_EMAILS don't count), and the homepage's anonymous AI import is closed. Existing
accounts sign in as usual, and nothing changes for attendees (apps, bundle API, /join/, /p/, Studio sites).
"""
from django.conf import settings

from console.accounts import normalize_email

INVITE_ONLY_TITLE = 'Attnd is invite-only right now'
INVITE_ONLY_MESSAGE = ("Attnd is invite-only right now and isn't taking new organizers yet. "
                       "If you were invited, sign up with the email address the invite was sent to.")


def signups_open():
    return bool(getattr(settings, 'SIGNUPS_OPEN', False))


def trial_import_open(request):
    """The homepage's "try it" AI import: open with SIGNUPS_OPEN, or for a signed-in organizer."""
    user = getattr(request, 'user', None)
    return signups_open() or bool(user and user.is_authenticated)


def is_invited(email):
    """A pending team invite for this email (any case). Only EventInvite counts."""
    email = normalize_email(email)
    if not email:
        return False
    from events.models import EventInvite
    return EventInvite.objects.filter(email__iexact=email).exists()


def may_sign_up(email):
    return signups_open() or is_invited(email)
