from django.forms import ModelForm, TextInput, DateTimeInput

from events.models import Notification


class NotificationForm(ModelForm):
    class Meta:
        model = Notification
        fields = ['title', 'body', 'send_time']
        widgets = {'title': TextInput(attrs={'class': 'form-control'}),
                   'body': TextInput(attrs={'class': 'form-control'}),
                   'send_time': DateTimeInput(attrs={'class': 'form-control', 'type': 'datetime-local', 'step': '1'}, format='%Y-%m-%dT%H:%M:%S')}
