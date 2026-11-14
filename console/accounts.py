"""Organizer accounts.

The account's identity is Django's own `username`, which we generate (`u` + 10 random hex characters)
and never show. People only ever see and sign in with their email, which they can change without
losing the account, its events or its team memberships.
"""
import secrets

from django.contrib.auth.models import User
from django.db.models import Q


def new_username():
    while True:
        name = 'u' + secrets.token_hex(5)
        if not User.objects.filter(username=name).exists():
            return name


def normalize_email(email):
    return (email or '').strip().lower()


def email_in_use(email, exclude=None):
    """True if any other account already uses this email (as its email, or as a legacy username)."""
    email = normalize_email(email)
    users = User.objects.filter(Q(email__iexact=email) | Q(username__iexact=email))
    if exclude is not None:
        users = users.exclude(pk=exclude.pk)
    return users.exists()


def create_account(email, password, **extra):
    return User.objects.create_user(username=new_username(), email=normalize_email(email), password=password, **extra)


def display_name(user):
    """What people see for an account: its email."""
    return user.email or user.username if user else ''


def claim_invites(user):
    """Turn pending team invitations for the user's email into memberships. Returns the events joined."""
    from events.models import EventInvite, EventMember
    if not user or not user.email:
        return []
    joined = []
    for invite in EventInvite.objects.filter(email__iexact=user.email).select_related('event'):
        if invite.event.user_id != user.id:
            EventMember.objects.get_or_create(event=invite.event, user=user, defaults={'role': invite.role})
            joined.append(invite.event)
        invite.delete()
    return joined
