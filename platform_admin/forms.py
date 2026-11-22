from django import forms

from platform_admin.models import EventOverride, PlatformSettings


class PricingForm(forms.ModelForm):
    new_account_daily_imports = forms.IntegerField(label='AI imports a day for new accounts', min_value=0)

    class Meta:
        model = PlatformSettings
        fields = ['standard_price_usd', 'studio_price_usd', 'free_device_limit']

    def clean(self):
        data = super().clean()
        for name in ('standard_price_usd', 'studio_price_usd'):
            if data.get(name) is not None and data[name] < 0:
                self.add_error(name, 'Use 0 or more.')
        return data


class OverrideForm(forms.ModelForm):
    PLANS = [('free', 'Free'), ('standard', 'Standard'), ('studio', 'Studio')]
    plan = forms.ChoiceField(choices=PLANS)
    listed = forms.BooleanField(label='Listed in nearby events', required=False)
    watching = forms.BooleanField(label='Website watching on', required=False)
    owner_ai_monthly_usd = forms.DecimalField(label="Owner's monthly AI credit (USD, empty = default)", required=False,
                                              min_value=0, max_digits=8, decimal_places=2)
    owner_ai_unlimited = forms.BooleanField(label="Owner's AI: no limit", required=False)

    class Meta:
        model = EventOverride
        fields = ['free_device_limit', 'no_free_limit', 'allow_publishing', 'studio_daily_cap_usd', 'notes']
        widgets = {'notes': forms.Textarea(attrs={'rows': 3})}
