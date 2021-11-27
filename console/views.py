import datetime
import os.path
from shutil import copyfile

import qrcode
from django.conf import settings
from django.contrib.auth import authenticate, login
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.models import User
from django.shortcuts import render, redirect
from django.urls import reverse
from django.views import View

from console.forms.event import EventForm
from console.forms.location import LocationForm
from console.forms.login import LoginForm
from console.forms.notification import NotificationForm
from console.forms.signup import SignUpForm
from events.models import Event, Location, Notification


class EventCreateView(LoginRequiredMixin, View):

    def get(self, request):
        first_event = Event.objects.filter(user=request.user).order_by('-start_time').first()
        return redirect('console:event', event_id=first_event.id)

    def post(self, request):
        form = EventForm(request.POST, request.FILES)
        form.instance.user = request.user
        event = form.save()

        url = 'https://www.google.com/?client=safari'
        qr = qrcode.QRCode(border=1)
        qr.add_data(url)
        qr.make(fit=True)

        image = qr.make_image()
        path = os.path.join(settings.MEDIA_ROOT, f'qr_{event.id}.png')
        image.save(path)

        return redirect(reverse('console:event', args=[event.id]))


class EventView(LoginRequiredMixin, View):

    def get(self, request, event_id):
        events = Event.objects.filter(user=request.user).order_by('-start_time')
        selected_event = Event.objects.filter(user=request.user, id=event_id).first()
        selected_event_form = EventForm(instance=selected_event)
        new_event_form = EventForm(auto_id='id_%s_new')

        context = {'events': events,
                   'selected_event': selected_event,
                   'selected_event_form': selected_event_form,
                   'new_event_form': new_event_form}

        return render(request, template_name='console/event.html', context=context)


class EventUpdateView(LoginRequiredMixin, View):

    def post(self, request, event_id):
        event = Event.objects.filter(id=event_id).first()
        form = EventForm(data=request.POST, files=request.FILES, instance=event)
        if form.is_valid():
            form.save()
            return redirect(reverse('console:event', args=[event_id]))


class EventDeleteView(LoginRequiredMixin, View):

    def post(self, request, event_id):
        Event.objects.filter(id=event_id, user=request.user).delete()
        return redirect(reverse('console:event_list'))


class LocationCreateView(LoginRequiredMixin, View):

    def post(self, request, event_id):
        form = LocationForm(data=request.POST, files=request.FILES)
        form.instance.user = request.user
        form.instance.event = Event.objects.filter(id=event_id, user=request.user).get()
        form.save()

        return redirect(reverse('console:location_list', args=[event_id]))


class LocationsListView(LoginRequiredMixin, View):

    def get(self, request, event_id):
        events = Event.objects.filter(user=request.user).order_by('-start_time')
        selected_event = Event.objects.filter(user=request.user, id=event_id).first()
        locations = Location.objects.filter(user=request.user, event_id=event_id)
        location_forms = [LocationForm(instance=location, auto_id=f'id_%s_{location.id}') for location in locations]
        new_location_form = LocationForm()
        new_event_form = EventForm(auto_id='id_%s_new')

        context = {'events': events,
                   'selected_event': selected_event,
                   'location_forms': location_forms,
                   'new_location_form': new_location_form,
                   'new_event_form': new_event_form}

        return render(request, template_name='console/locations.html', context=context)


class LocationUpdateView(LoginRequiredMixin, View):

    def post(self, request, event_id, location_id):
        location = Location.objects.filter(id=location_id, user=request.user).first()
        LocationForm(data=request.POST, files=request.FILES, instance=location).save()
        return redirect(reverse('console:location_list', args=[event_id]))


class LocationDeleteView(LoginRequiredMixin, View):

    def get(self, request, event_id, location_id):
        Location.objects.filter(id=location_id, user=request.user).delete()
        return redirect(reverse('console:location_list', args=[event_id]))


class NotificationCreateView(LoginRequiredMixin, View):

    def post(self, request, event_id):
        form = NotificationForm(data=request.POST)
        form.instance.event = Event.objects.filter(id=event_id, user=request.user).get()
        form.instance.user = request.user
        form.save()

        return redirect(reverse('console:notification_list', args=[event_id]))


class NotificationsListView(LoginRequiredMixin, View):

    def get(self, request, event_id):
        events = Event.objects.filter(user=request.user).order_by('-start_time')
        selected_event = Event.objects.filter(user=request.user, id=event_id).first()
        notifications = Notification.objects.filter(user=request.user, event_id=event_id).order_by('-send_time')
        notification_forms = [NotificationForm(instance=notification) for notification in notifications]
        new_notification_form = NotificationForm()
        new_event_form = EventForm(auto_id='id_%s_new')

        context = {'events': events,
                   'selected_event': selected_event,
                   'notification_forms': notification_forms,
                   'new_notification_form': new_notification_form,
                   'new_event_form': new_event_form}

        return render(request, template_name='console/notifications.html', context=context)


class NotificationUpdateView(LoginRequiredMixin, View):

    def post(self, request, event_id, notification_id):
        notification = Notification.objects.filter(id=notification_id, user=request.user, event_id=event_id).first()
        NotificationForm(data=request.POST, instance=notification).save()
        return redirect(reverse('console:notification_list', args=[event_id]))


class NotificationDeleteView(LoginRequiredMixin, View):

    def get(self, request, event_id, notification_id):
        Notification.objects.filter(id=notification_id, event_id=event_id, user=request.user).delete()
        return redirect(reverse('console:notification_list', args=[event_id]))


class AnalyticsView(LoginRequiredMixin, View):

    def get(self, request, event_id):
        events = Event.objects.filter(user=request.user).order_by('-start_time')
        selected_event = Event.objects.filter(user=request.user, id=event_id).first()
        new_event_form = EventForm(auto_id='id_%s_new')

        context = {'events': events,
                   'selected_event': selected_event,
                   'new_event_form': new_event_form}

        return render(request, template_name='console/analytics.html', context=context)


class BillingView(LoginRequiredMixin, View):

    def get(self, request, event_id):
        events = Event.objects.filter(user=request.user).order_by('-start_time')
        selected_event = Event.objects.filter(user=request.user, id=event_id).first()
        new_event_form = EventForm(auto_id='id_%s_new')

        context = {'events': events,
                   'selected_event': selected_event,
                   'new_event_form': new_event_form}

        return render(request, template_name='console/billing.html', context=context)


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
            user.is_staff = False
            login(request, user)
            self.create_demo_event(user=user)
            return redirect('console:create_first_event')
        else:
            context = {'form': form}
            return render(request, template_name='registration/signup.html', context=context)

    def create_demo_event(self, user: User):
        event_name = f'Demo Event - {user.username}'
        event_code = f'{user.username}-demo-event'
        event_description = 'The local art fair is back! Artists will be teaching classes and selling paintings'
        event_latitude = 32.754695
        event_longitude = -97.331605
        event_start_time = datetime.datetime.now() + datetime.timedelta(weeks=1)
        copyfile(os.path.join(settings.BASE_DIR, 'console', 'static', 'console', 'demo_art_fair_flyer.pdf'), os.path.join(settings.MEDIA_ROOT, f'{user.username}_demo_art_fair_flyer.pdf'))
        event_flyer = f'{user.username}_demo_art_fair_flyer.pdf'
        event_address = '420 Main St, Fort Worth, TX 76102, USA'
        event_user = user
        Event.objects.create(name=event_name, event_code=event_code, description=event_description, latitude=event_latitude, longitude=event_longitude, start_time=event_start_time, flyer=event_flyer, address=event_address, user=event_user)


class LoginView(View):

    def get(self, request):
        context = {'form': LoginForm()}
        return render(request, template_name='registration/login.html', context=context)

    def post(self, request):
        form = LoginForm(request=request, data=request.POST)
        if form.is_valid():
            username = form.cleaned_data.get('username')
            password = form.cleaned_data.get('password')
            user = authenticate(username=username, password=password)
            if user and user.is_active:
                login(request, user)
                return redirect(settings.LOGIN_REDIRECT_URL)
        else:
            context = {'form': form}
            return render(request, template_name='registration/login.html', context=context)
