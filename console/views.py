from django.contrib.auth import authenticate, login
from django.shortcuts import render, redirect
from events.models import Event, Location, Notification
from django.contrib.auth.mixins import LoginRequiredMixin
from console.forms.signup import SignUpForm

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


class SignUpView(View):

    def get(self, request):
        context = {'form': SignUpForm()}
        return render(request, template_name='console/signup.html', context=context)

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
            return render(request, template_name='console/signup.html', context=context)

