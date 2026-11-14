from django.contrib.auth.signals import user_logged_in
from django.dispatch import receiver

from console.accounts import claim_invites


@receiver(user_logged_in)
def claim_pending_invites(sender, request, user, **kwargs):
    """Invitations sent to this email before the person had an account become memberships."""
    claim_invites(user)
