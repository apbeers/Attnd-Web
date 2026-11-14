import datetime
import logging

from django.contrib.auth import login
from django.contrib.auth.models import User
from django.db import IntegrityError
from django.db.models import Q
from django.shortcuts import render, redirect
from django.utils import timezone
from django.views import View

from console.forms.signup import SignUpForm
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

        if not form.is_valid():
            return render(request, 'registration/signup.html', {
                'form': form,
                'claim_code': claim,
            })

        email = form.cleaned_data['email'].strip().lower()
        password = form.cleaned_data['password']

        if User.objects.filter(Q(username__iexact=email) | Q(email__iexact=email)).exists():
            form.add_error('email', 'An account with this email already exists.')
            return render(request, 'registration/signup.html', {
                'form': form,
                'claim_code': claim,
            })

        try:
            user = User.objects.create_user(username=email[:150], email=email, password=password)
        except IntegrityError:
            form.add_error('email', 'An account with this email already exists.')
            return render(request, 'registration/signup.html', {
                'form': form,
                'claim_code': claim,
            })

        login(request, user)

        # If we have a claim code, transfer the trial event to the new account.
        if claim:
            event = Event.objects.filter(event_code=claim).first() if _claim_trial_event(user, claim) else None
            if event is not None:
                return redirect('console:event', event_id=event.pk)
        return redirect('console:dashboard')
