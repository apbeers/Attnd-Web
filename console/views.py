import datetime

from django.contrib.auth import authenticate, login
from django.utils import timezone
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
        Event.objects.create(
            name=f'Demo Event - {user.username}',
            event_code=f'{user.username}-demo',
            description='The local art fair is back! Artists will be teaching classes and selling paintings.',
            latitude=32.754695,
            longitude=-97.331605,
            start_time=timezone.now() + datetime.timedelta(weeks=1),
            flyer='',
            address='420 Main St, Fort Worth, TX 76102, USA',
            order_id='',
            user=user,
        )
