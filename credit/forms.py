from decimal import Decimal

from django import forms
from django.contrib.auth import get_user_model

from credit.models import AiLimits


class AiLimitsForm(forms.ModelForm):
    class Meta:
        model = AiLimits
        fields = ['monthly_default_usd', 'visitor_allowance_usd', 'platform_alert_usd', 'agent_daily_usd',
                  'studio_daily_usd', 'place_images_daily_usd', 'covers_daily_usd', 'prospects_daily_usd',
                  'new_account_daily_imports']
        labels = {'monthly_default_usd': 'Monthly AI credit per person (USD)',
                  'visitor_allowance_usd': 'Allowance before signing up (USD)',
                  'platform_alert_usd': 'Email me when all AI this month passes (USD)',
                  'agent_daily_usd': 'Ask Attnd per person per day (USD)',
                  'studio_daily_usd': 'Studio per event per day (USD)',
                  'place_images_daily_usd': 'Place photos, all events per day (USD)',
                  'covers_daily_usd': 'Event covers, all events per day (USD)',
                  'prospects_daily_usd': 'Prospects discovery per day (USD)',
                  'new_account_daily_imports': 'AI imports a day for new accounts'}
        help_texts = {'monthly_default_usd': 'Resets on the 1st (UTC). Overrides below change it for one person.',
                      'platform_alert_usd': '0 = no email.'}

    def clean(self):
        data = super().clean()
        for name, value in data.items():
            if isinstance(value, Decimal) and value < 0:
                self.add_error(name, 'Use 0 or more.')
        return data


class OverrideForm(forms.Form):
    email = forms.EmailField(label='Person (email)')
    monthly_usd = forms.DecimalField(label='Monthly credit (USD)', required=False, min_value=0, max_digits=8,
                                     decimal_places=2, help_text='Leave empty with No limit ticked.')
    unlimited = forms.BooleanField(label='No limit', required=False)
    note = forms.CharField(label='Note', required=False, max_length=200)

    def clean_email(self):
        email = self.cleaned_data['email']
        user = get_user_model().objects.filter(email__iexact=email).first()
        if user is None:
            raise forms.ValidationError('No account uses that email.')
        self.user = user
        return email

    def clean(self):
        data = super().clean()
        if not data.get('unlimited') and data.get('monthly_usd') is None and not self.errors:
            self.add_error('monthly_usd', 'Give an amount, or tick No limit.')
        return data
