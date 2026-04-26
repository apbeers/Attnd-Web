import datetime

from django.contrib.auth import login
from django.contrib.auth.models import User
from django.db import IntegrityError
from django.shortcuts import render, redirect
from django.utils import timezone
from django.views import View

from console.forms.signup import SignUpForm
from events.models import Event


class SignUpView(View):

    def get(self, request):
        return render(request, 'registration/signup.html', {'form': SignUpForm()})

    def post(self, request):
        form = SignUpForm(request.POST)
        if not form.is_valid():
            return render(request, 'registration/signup.html', {'form': form})

        email = form.cleaned_data['email']
        password = form.cleaned_data['password']

        if User.objects.filter(username=email[:150]).exists():
            form.add_error('email', 'An account with this email already exists.')
            return render(request, 'registration/signup.html', {'form': form})

        try:
            user = User.objects.create_user(username=email[:150], email=email, password=password)
        except IntegrityError:
            form.add_error('email', 'An account with this email already exists.')
            return render(request, 'registration/signup.html', {'form': form})

        user.is_staff = True
        user.save()
        login(request, user)
        self.create_demo_event(user=user)
        return redirect('/admin/')

    @staticmethod
    def create_demo_event(user: User):
        Event.objects.create(
            name=f'Demo Event - {user.email}',
            event_code=f'{user.username[:140]}-demo',
            description='The local art fair is back! Artists will be teaching classes and selling paintings.',
            latitude=32.754695,
            longitude=-97.331605,
            start_time=timezone.now() + datetime.timedelta(weeks=1),
            flyer='',
            address='420 Main St, Fort Worth, TX 76102, USA',
            order_id='',
            user=user,
        )
