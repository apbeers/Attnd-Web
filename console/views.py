import datetime
import logging

from django.contrib.auth import login
from django.contrib.auth.models import User
from django.db import IntegrityError
from django.shortcuts import render, redirect
from django.utils import timezone
from django.views import View

from console.forms.signup import SignUpForm
from events.models import Event, Location, Notification, generate_event_code

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
    event.save()

    Location.objects.filter(event=event).update(user=user)
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

        email = form.cleaned_data['email']
        password = form.cleaned_data['password']

        if User.objects.filter(username=email[:150]).exists():
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

        user.is_staff = True
        user.save()
        login(request, user)

        # If we have a claim code, transfer the trial event; otherwise seed a demo event.
        if claim and _claim_trial_event(user, claim):
            pass  # event transferred — nothing else to do
        else:
            self.create_demo_event(user=user)

        return redirect('/admin/')

    @staticmethod
    def create_demo_event(user: User):
        Event.objects.create(
            name=f'Demo Event - {user.email}',
            event_code=generate_event_code(),
            description='The local art fair is back! Artists will be teaching classes and selling paintings.',
            latitude=32.754695,
            longitude=-97.331605,
            start_time=timezone.now() + datetime.timedelta(weeks=1),
            flyer='',
            address='420 Main St, Fort Worth, TX 76102, USA',
            order_id='',
            user=user,
        )
