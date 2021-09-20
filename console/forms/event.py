from django.forms import ModelForm, DateTimeInput, TextInput, Textarea, URLInput, BooleanField, FileInput, ClearableFileInput, HiddenInput

from events.models import Event


class EventForm(ModelForm):

    def __init__(self, *args, **kwargs):
        kwargs.setdefault('label_suffix', '')  # globally override the Django >=1.6 default of ':'
        super(EventForm, self).__init__(*args, **kwargs)

    class Meta:
        model = Event
        fields = ['name', 'event_code', 'description', 'address', 'start_time', 'flyer', 'latitude', 'longitude']
        widgets = {
            'name': TextInput(attrs={'class': 'form-control'}),
            'event_code': TextInput(attrs={'class': 'form-control'}),
            'description': Textarea(attrs={'class': 'form-control', 'rows': 5, 'cols': 20}),
            'address': TextInput(attrs={'class': 'form-control'}),
            'start_time': DateTimeInput(attrs={'class': 'form-control', 'type': 'datetime-local'}, format='%Y-%m-%dT%H:%M:%S'),
            'change': TextInput(),
            'flyer': ClearableFileInput(attrs={'class': 'form-control'}),
            'latitude': HiddenInput(),
            'longitude': HiddenInput()
        }
        help_texts = {
            'event_code': 'People can enter this code in the app to join the event'
        }


