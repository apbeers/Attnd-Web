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
from console import twofactor
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

    def get(self, request):
        claim = request.GET.get('claim', '')
        return render(request, 'registration/signup.html', {
            'form': SignUpForm(),
            'claim_code': claim,
        })

    def post(self, request):
        form = SignUpForm(request.POST)
        claim = request.POST.get('claim', '').strip()
        if signup_limited(request):
            return render(request, 'registration/signup.html', {
                'form': form, 'claim_code': claim, 'rate_limited': SIGNUP_LIMITED}, status=429)

        if not form.is_valid():
            return render(request, 'registration/signup.html', {
                'form': form,
                'claim_code': claim,
            })

        email = form.cleaned_data['email'].strip().lower()
        password = form.cleaned_data['password']

        if email_in_use(email):
            form.add_error('email', 'An account with this email already exists.')
            return render(request, 'registration/signup.html', {
                'form': form,
                'claim_code': claim,
            })

        try:
            user = create_account(email, password)
        except IntegrityError:
            form.add_error('email', 'An account with this email already exists.')
            return render(request, 'registration/signup.html', {
                'form': form,
                'claim_code': claim,
            })

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
    for field in password_form.fields.values():
        field.widget.attrs['class'] = 'form-control'
    recovery_codes = None
    if request.method == 'POST' and request.POST.get('action') in ('2fa_disable', '2fa_codes'):
        recovery_codes, response = account_two_factor_action(request)
        if response is not None:
            return response
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
        for field in password_form.fields.values():
            field.widget.attrs['class'] = 'form-control'
        if password_form.is_valid():
            user = password_form.save()
            update_session_auth_hash(request, user)
            messages.success(request, 'Password changed.')
            return redirect('console:account')
    device = twofactor.confirmed_device(request.user)
    return render(request, 'console/account.html', {
        'email_form': email_form, 'password_form': password_form, 'section': 'account',
        'two_factor': device, 'two_factor_required': twofactor.is_required(request.user),
        'recovery_codes': recovery_codes,
        'recovery_codes_left': len(device.recovery_codes) if device else 0,
    })
