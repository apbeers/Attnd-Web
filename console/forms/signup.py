from django import forms


class SignUpForm(forms.Form):
    email = forms.EmailField(widget=forms.EmailInput(attrs={
        'autocomplete': 'email',
        'name': 'username',  # password managers look for this
        'autofocus': True,
    }))
    password = forms.CharField(widget=forms.PasswordInput(attrs={
        'autocomplete': 'new-password',
    }))


class EmailChangeForm(forms.Form):
    email = forms.EmailField(label='New email', widget=forms.EmailInput(attrs={'autocomplete': 'email'}))
    password = forms.CharField(label='Current password', widget=forms.PasswordInput(attrs={'autocomplete': 'current-password'}))

    def __init__(self, user, *args, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

    def clean_password(self):
        if not self.user.check_password(self.cleaned_data['password']):
            raise forms.ValidationError('That password is not right.')
        return self.cleaned_data['password']

    def clean_email(self):
        from console.accounts import email_in_use, normalize_email
        email = normalize_email(self.cleaned_data['email'])
        if email_in_use(email, exclude=self.user):
            raise forms.ValidationError('Another account already uses that email.')
        return email
