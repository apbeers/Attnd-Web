import datetime
import os.path
from shutil import copyfile

from django.conf import settings
from django.contrib.auth import authenticate, login
from django.contrib.auth.models import User
from django.shortcuts import render, redirect
from django.views import View

from console.forms.signup import SignUpForm
from events.models import Event


class SignUpView(View):

    def get(self, request):
        context = {'form': SignUpForm()}
        return render(request, template_name='registration/signup.html', context=context)

    def post(self, request):
        form = SignUpForm(request.POST)
        if form.is_valid():
            form.save()
            username = form.cleaned_data.get('username')
            raw_password = form.cleaned_data.get('password1')
            user = authenticate(username=username, password=raw_password)
            user.is_staff = True
            user.save()
            login(request, user)
            self.create_demo_event(user=user)
            return redirect('/admin/')
        else:
            context = {'form': form}
            return render(request, template_name='registration/signup.html', context=context)

    @staticmethod
    def create_demo_event(user: User):
        event_name = f'Demo Event - {user.username}'
        event_code = f'{user.username}-demo-event'
        event_description = 'The local art fair is back! Artists will be teaching classes and selling paintings'
        event_latitude = 32.754695
        event_longitude = -97.331605
        event_start_time = datetime.datetime.now() + datetime.timedelta(weeks=1)
        copyfile(
            os.path.join(settings.BASE_DIR, 'console', 'static', 'console', 'demo_art_fair_flyer.pdf'),
            os.path.join(settings.MEDIA_ROOT, f'{user.username}_demo_art_fair_flyer.pdf'),
        )
        event_flyer = f'{user.username}_demo_art_fair_flyer.pdf'
        Event.objects.create(
            name=event_name, event_code=event_code, description=event_description,
            latitude=event_latitude, longitude=event_longitude, start_time=event_start_time,
            flyer=event_flyer, address='420 Main St, Fort Worth, TX 76102, USA',
            order_id='', user=user,
        )
