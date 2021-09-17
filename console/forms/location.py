from django.forms import ModelForm, TextInput, Textarea, ClearableFileInput, URLInput, NumberInput

from events.models import Location

attrs = {'class': 'form-control'}


class LocationForm(ModelForm):
    class Meta:
        model = Location
        fields = '__all__'
        widgets = {
            'name': TextInput(attrs),
            'snippet': TextInput(attrs),
            'description': Textarea(attrs={'class': 'form-control', 'rows': 5, 'cols': 20}),
            'image': ClearableFileInput(attrs),
            'website': URLInput(attrs),
            'latitude': NumberInput(attrs),
            'longitude': NumberInput(attrs),
        }
        help_texts = {
            'snippet': 'This will show on the map when a user clicks the pin for this location'
        }
