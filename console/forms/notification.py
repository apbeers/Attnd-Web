from datetime import datetime, timezone


from django.forms import ModelForm, TextInput, DateTimeInput, forms

from events.models import Notification


class NotificationForm(ModelForm):
    # def clean_send_time(self):
    #     send_time = self.cleaned_data['send_time']
    #     if send_time < timezone.
    #         raise forms.ValidationError("The send time cannot be in the past")
    #     return send_time

    class Meta:
        model = Notification
        fields = '__all__'
        widgets = {'title': TextInput(attrs={'class': 'form-control'}),
                   'body': TextInput(attrs={'class': 'form-control'}),
                   'send_time': DateTimeInput(attrs={'class': 'form-control', 'type': 'datetime-local'}, format='%Y-%m-%dT%H:%M:%S')}
