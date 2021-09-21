from django.forms import ModelForm, TextInput, Textarea, ClearableFileInput, URLInput, NumberInput, HiddenInput

from events.models import Location

attrs = {'class': 'form-control'}


class LocationForm(ModelForm):
    class Meta:
        model = Location
        fields = ['name', 'snippet', 'description', 'address', 'image', 'website', 'latitude', 'longitude']
        widgets = {
            'name': TextInput(attrs),
            'snippet': TextInput(attrs),
            'description': Textarea(attrs={'class': 'form-control', 'rows': 5, 'cols': 20}),
            'address': TextInput(attrs),
            'image': ClearableFileInput(attrs),
            'website': URLInput(attrs),
            'latitude': HiddenInput(),
            'longitude': HiddenInput(),
        }
        help_texts = {
            'snippet': 'This will show on the map when a user clicks the pin for this location'
        }
