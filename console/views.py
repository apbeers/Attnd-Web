from django.shortcuts import render
from events.models import Event, Location, Notification
from django.contrib.auth.mixins import LoginRequiredMixin


# Create your views here.
from django.views import View


def home(request):
    return render(request, template_name='console/home.html')


class DashboardView(LoginRequiredMixin, View):

    def get(self, request):
        events = Event.objects.filter(user=request.user)
        locations = Location.objects.filter(user=request.user)
        notifications = Notification.objects.filter(user=request.user)

        context = {'events': events,
                   'locations': locations,
                   'notifications': notifications}

        return render(request, template_name='console/dashboard.html', context=context)
