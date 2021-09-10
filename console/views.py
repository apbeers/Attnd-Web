from django.contrib.auth import authenticate, login
from django.shortcuts import render, redirect
from events.models import Event, Location, Notification
from django.contrib.auth.mixins import LoginRequiredMixin
from console.forms.signup import SignUpForm

# Create your views here.
from django.views import View


def home(request):
    return render(request, template_name='console/home.html')


class EventView(LoginRequiredMixin, View):

    def get(self, request, event_id=None):
        if not event_id:
            first_event = Event.objects.filter(user=request.user).order_by('-start_date').first()
            return redirect('event', event_id=first_event.id)

        events = Event.objects.filter(user=request.user).order_by('-start_date')
        selected_event = Event.objects.filter(user=request.user, id=event_id).first()

        context = {'events': events,
                   'selected_event': selected_event}

        return render(request, template_name='console/details.html', context=context)


class LocationsView(LoginRequiredMixin, View):

    def get(self, request, event_id):
        events = Event.objects.filter(user=request.user).order_by('-start_date')
        selected_event = Event.objects.filter(user=request.user, id=event_id).first()
        locations = Location.objects.filter(user=request.user, event_id=event_id)

        context = {'events': events,
                   'selected_event': selected_event,
                   'locations': locations}

        return render(request, template_name='console/locations.html', context=context)


class NotificationsView(LoginRequiredMixin, View):

    def get(self, request, event_id):
        events = Event.objects.filter(user=request.user).order_by('-start_date')
        selected_event = Event.objects.filter(user=request.user, id=event_id).first()
        notifications = Notification.objects.filter(user=request.user, event_id=event_id)

        context = {'events': events,
                   'selected_event': selected_event,
                   'notifications': notifications}

        return render(request, template_name='console/notifications.html', context=context)


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

