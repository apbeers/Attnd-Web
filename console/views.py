import datetime
import logging

from django.contrib import messages
from django.contrib.auth import login, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import PasswordChangeForm
from django.contrib.auth.models import User
from django.db import IntegrityError
from django.shortcuts import render, redirect
from django.utils import timezone
from django.views import View

from console.accounts import claim_invites, create_account, email_in_use
from console.forms.signup import EmailChangeForm, SignUpForm
from console import passkeys, twofactor, webauthn
from console.mail import email_enabled
from console.models import Passkey
from console.signups import INVITE_ONLY_MESSAGE, INVITE_ONLY_TITLE, may_sign_up, signups_open
from console.views_auth import SIGNUP_LIMITED, account_two_factor_action, signup_limited
from events.models import Event, Location, Notification, ScheduledItem, generate_event_code

logger = logging.getLogger(__name__)

TRIAL_USERNAME = "trialuser"


def _claim_trial_event(user: User, event_code: str) -> bool:
    """
    Transfer a trial event (owned by trialuser) to the given user.
    Also transfers all associated Locations and Notifications.
    Returns True if the claim succeeded, False if the event wasn't found
    or doesn't belong to the trial user.
    """
    try:
        trial_user = User.objects.get(username=TRIAL_USERNAME)
        event = Event.objects.get(event_code=event_code, user=trial_user)
    except (User.DoesNotExist, Event.DoesNotExist):
        logger.warning("Claim failed: event_code=%r not found under trialuser", event_code)
        return False

    event.user = user
    event.save(update_fields=['user'])

    Location.objects.filter(event=event).update(user=user)
    ScheduledItem.objects.filter(event=event).update(user=user)
    Notification.objects.filter(event=event).update(user=user)

    logger.info("Claimed event %r (code=%s) → user %s", event.name, event_code, user.email)
    return True


class SignUpView(View):
    """Create an organizer account. While SIGNUPS_OPEN is off only invited emails may (console.signups); everyone
    else gets the invite-only page, worded the same whether or not the email has an account or an invite."""

    def _render(self, request, form, claim, status=200, **extra):
        return render(request, 'registration/signup.html', {
            'form': form, 'claim_code': claim, 'signups_open': signups_open(),
            'invite_only_title': INVITE_ONLY_TITLE, 'invite_only_message': INVITE_ONLY_MESSAGE, **extra,
        }, status=status)

    def get(self, request):
        return self._render(request, SignUpForm(), request.GET.get('claim', ''))

    def post(self, request):
        form = SignUpForm(request.POST)
        claim = request.POST.get('claim', '').strip()
        if signup_limited(request):
            return self._render(request, form, claim, status=429, rate_limited=SIGNUP_LIMITED, form_open=True)

        if not form.is_valid():
            return self._render(request, form, claim, form_open=True)

        email = form.cleaned_data['email'].strip().lower()
        password = form.cleaned_data['password']

        if not may_sign_up(email):
            # Checked before "already exists", so this answer never tells who has an account or an invite.
            logger.info("Sign-up refused (invite only)")
            return self._render(request, SignUpForm(initial={'email': email}), claim, status=403,
                                refused=INVITE_ONLY_MESSAGE, form_open=True)

        if email_in_use(email):
            form.add_error('email', 'An account with this email already exists.')
            return self._render(request, form, claim, form_open=True)

        try:
            user = create_account(email, password)
        except IntegrityError:
            form.add_error('email', 'An account with this email already exists.')
            return self._render(request, form, claim, form_open=True)

        login(request, user)  # pending team invites for this email are claimed on login (console.signals)

        # If we have a claim code, transfer the trial event to the new account.
        if claim:
            event = Event.objects.filter(event_code=claim).first() if _claim_trial_event(user, claim) else None
            if event is not None:
                return redirect('console:event', event_id=event.pk)
        return redirect('console:dashboard')


@login_required
def account(request):
    """Change the email you sign in with, or your password. The account (and its events) stays the same."""
    email_form = EmailChangeForm(request.user, prefix='email')
    password_form = PasswordChangeForm(request.user, prefix='pw')
    recovery_codes = None
    action = request.POST.get('action') if request.method == 'POST' else None
    if action in ('2fa_disable', '2fa_codes', 'email_codes_on', 'email_codes_off'):
        recovery_codes, response = account_two_factor_action(request)
        if response is not None:
            return response
    elif action in ('passkey_rename', 'passkey_remove'):
        return _passkey_action(request, action)
    elif request.method == 'POST' and request.POST.get('action') == 'email':
        email_form = EmailChangeForm(request.user, request.POST, prefix='email')
        if email_form.is_valid():
            request.user.email = email_form.cleaned_data['email']
            request.user.save(update_fields=['email'])
            claim_invites(request.user)
            messages.success(request, f'You now sign in with {request.user.email}.')
            return redirect('console:account')
    elif request.method == 'POST' and request.POST.get('action') == 'password':
        password_form = PasswordChangeForm(request.user, request.POST, prefix='pw')
        if password_form.is_valid():
            user = password_form.save()
            update_session_auth_hash(request, user)
            messages.success(request, 'Password changed.')
            return redirect('console:account')
    user = request.user
    device = twofactor.confirmed_device(user)
    keys = list(Passkey.objects.filter(user=user))
    required = twofactor.is_required(user)
    for key in keys:
        key.removable = passkeys.can_remove(user, key)
    return render(request, 'console/account.html', {
        'email_form': email_form, 'password_form': password_form, 'section': 'account',
        'two_factor': device, 'two_factor_required': required,
        'recovery_codes': recovery_codes,
        'recovery_codes_left': twofactor.recovery_codes_left(user),
        'passkeys': keys, 'passkey_name': webauthn.default_name(request.META.get('HTTP_USER_AGENT', '')),
        'has_second_factor': twofactor.has_second_factor(user),
        'email_codes_on': twofactor.email_codes_on(user), 'email_available': email_enabled() and bool(user.email),
        'totp_removable': device is not None and (not required or bool(keys)),
    })


def _passkey_action(request, action):
    """Rename or remove one of your passkeys. Staff can't remove their last strong method."""
    passkey = Passkey.objects.filter(user=request.user, pk=request.POST.get('passkey') or 0).first()
    if passkey is None:
        messages.error(request, 'That passkey is gone already.')
    elif action == 'passkey_rename':
        passkey.name = passkeys.clean_name(request.POST.get('name', ''), request.META.get('HTTP_USER_AGENT', ''))
        passkey.save(update_fields=['name'])
        messages.success(request, f'Renamed to “{passkey.name}”.')
    elif not passkeys.can_remove(request.user, passkey):
        messages.error(request, 'Admin accounts need a passkey or an authenticator app. '
                                'Add another one before removing this passkey.')
    else:
        passkey.delete()
        twofactor.after_factor_removed(request.user)
        messages.success(request, f'Passkey “{passkey.name}” removed. Also delete it from the device or '
                                  'password manager that holds it.')
    return redirect('console:account')
