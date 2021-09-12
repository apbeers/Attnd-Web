from django import forms
from django.forms import ModelForm

from events.models import Notification


class NotificationForm(ModelForm):
    class Meta:
        model = Notification
        fields = '__all__'

