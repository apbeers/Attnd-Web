from django.contrib.auth import authenticate, login
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import render, redirect
from django.urls import reverse
from django.views import View

from console.forms.event import EventForm
from console.forms.notification import NotificationForm
from console.forms.signup import SignUpForm
from events.models import Event, Location, Notification


def home(request):
    return render(request, template_name='console/home.html')


class EventView(LoginRequiredMixin, View):

    def get(self, request, event_id=None):
        if not event_id:
            first_event = Event.objects.filter(user=request.user).order_by('-start_date').first()
            return redirect('event', event_id=first_event.id)

        events = Event.objects.filter(user=request.user).order_by('-start_date')
        selected_event = Event.objects.filter(user=request.user, id=event_id).first()
        # file_data = {'flyer', SimpleUploadedFile(selected_event.flyer.name, content=selected_event.flyer)}
        # form = EventForm(data=model_to_dict(selected_event), files=file_data)

        context = {'events': events,
                   'selected_event': selected_event}

        return render(request, template_name='console/event.html', context=context)

    def post(self, request, event_id):
        event = Event.objects.filter(id=event_id).get()
        form = EventForm(data=request.POST, files=request.FILES, instance=event)
        if form.is_valid():
            form.save()
            return redirect(reverse('event', args=[event_id]))
        else:
            print(form.errors)


class LocationsView(LoginRequiredMixin, View):

    def get(self, request, event_id):
        events = Event.objects.filter(user=request.user).order_by('-start_date')
        selected_event = Event.objects.filter(user=request.user, id=event_id).first()
        locations = Location.objects.filter(user=request.user, event_id=event_id)

        context = {'events': events,
                   'selected_event': selected_event,
                   'locations': locations}

        return render(request, template_name='console/locations.html', context=context)


# Notification Views
class NotificationsListView(LoginRequiredMixin, View):

    def get(self, request, event_id):
        events = Event.objects.filter(user=request.user).order_by('-start_date')
        selected_event = Event.objects.filter(user=request.user, id=event_id).first()
        notifications = Notification.objects.filter(user=request.user, event_id=event_id).order_by('-send_time')
        notification_forms = [NotificationForm(instance=notification) for notification in notifications]
        new_notification_form = NotificationForm()

        context = {'events': events,
                   'selected_event': selected_event,
                   'notification_forms': notification_forms,
                   'new_notification_form': new_notification_form}

        return render(request, template_name='console/notifications.html', context=context)


class NotificationCreateView(View):

    def post(self, request, event_id):
        event = Event.objects.filter(id=event_id, user=request.user).get()
        user = request.user
        title = request.POST.get('title')
        body = request.POST.get('body')
        send_time = request.POST.get('send_time')
        Notification.objects.create(event=event, user=user, title=title, body=body, send_time=send_time)
        return redirect(reverse('notification_list', args=[event_id]))


class NotificationUpdateView(View):

    def post(self, request, event_id, notification_id):
        notification = Notification.objects.filter(id=notification_id, user=request.user, event_id=event_id).first()
        NotificationForm(data=request.POST, instance=notification).save()
        return redirect(reverse('notification_list', args=[event_id]))


class NotificationDeleteView(View):

    def post(self, request, event_id, notification_id):
        Notification.objects.filter(id=notification_id, event_id=event_id, user=request.user).delete()
        return redirect(reverse('notification_list', args=[event_id]))


class AnalyticsView(LoginRequiredMixin, View):

    def get(self, request, event_id):
        events = Event.objects.filter(user=request.user).order_by('-start_date')
        selected_event = Event.objects.filter(user=request.user, id=event_id).first()

        context = {'events': events,
                   'selected_event': selected_event}

        return render(request, template_name='console/analytics.html', context=context)


class BillingView(LoginRequiredMixin, View):

    def get(self, request, event_id):
        events = Event.objects.filter(user=request.user).order_by('-start_date')
        selected_event = Event.objects.filter(user=request.user, id=event_id).first()

        context = {'events': events,
                   'selected_event': selected_event}

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
            return redirect('dashboard')
        else:
            context = {'form': form}
            return render(request, template_name='registration/signup.html', context=context)
