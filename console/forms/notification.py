from django.forms import ModelForm, TextInput, DateTimeInput

from events.models import Notification


class NotificationForm(ModelForm):
    class Meta:
        model = Notification
        fields = '__all__'
        widgets = {'title': TextInput(attrs={'class': 'form-control'}),
                   'body': TextInput(attrs={'class': 'form-control'}),
                   'send_time': DateTimeInput(attrs={'class': 'form-control', 'type': 'datetime-local'}, format='%Y-%m-%dT%H:%M:%S')}
