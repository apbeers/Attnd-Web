import datetime
import logging

from attnd.permissions import signed_in
from django.contrib import messages
from django.contrib.auth import login, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import PasswordChangeForm
from django.db import IntegrityError
from django.shortcuts import render, redirect
from django.urls import reverse
from django.utils import timezone
from django.views import View

from console.accounts import claim_invites, create_account, email_in_use
from console.forms.signup import EmailChangeForm, SignUpForm
from console import passkeys, twofactor, webauthn
from console.mail import email_enabled
from console.models import Passkey
from console.signups import INVITE_ONLY_MESSAGE, INVITE_ONLY_TITLE, may_sign_up, signups_open
from console.views_auth import SIGNUP_LIMITED, account_two_factor_action, signup_limited
from events.models import Event, generate_event_code

logger = logging.getLogger(__name__)

class SignUpView(View):
    """Create an organizer account. While SIGNUPS_OPEN is off only invited emails may (console.signups); everyone
    else gets the invite-only page, worded the same whether or not the email has an account or an invite."""

    def _render(self, request, form, status=200, **extra):
        return render(request, 'registration/signup.html', {
            'form': form, 'signups_open': signups_open(),
            'invite_only_title': INVITE_ONLY_TITLE, 'invite_only_message': INVITE_ONLY_MESSAGE, **extra,
        }, status=status)

    def get(self, request):
        if request.user.is_authenticated:  # already signed in: the console
            return redirect('console:dashboard')
        return self._render(request, SignUpForm())

    def post(self, request):
        form = SignUpForm(request.POST)
        if signup_limited(request):
            return self._render(request, form, status=429, rate_limited=SIGNUP_LIMITED, form_open=True)

        if not form.is_valid():
            return self._render(request, form, form_open=True)

        email = form.cleaned_data['email'].strip().lower()
        password = form.cleaned_data['password']

        if not may_sign_up(email):
            # Checked before "already exists", so this answer never tells who has an account or an invite.
            logger.info("Sign-up refused (invite only)")
            return self._render(request, SignUpForm(initial={'email': email}), status=403,
                                refused=INVITE_ONLY_MESSAGE, form_open=True)

        if email_in_use(email):
            form.add_error('email', 'An account with this email already exists.')
            return self._render(request, form, form_open=True)

        try:
            user = create_account(email, password)
        except IntegrityError:
            form.add_error('email', 'An account with this email already exists.')
            return self._render(request, form, form_open=True)

        login(request, user)  # pending team invites for this email are claimed on login (console.signals)
        return redirect('console:dashboard')


# Account changes that need a recent sign-in (console/recent_auth.py). Renaming a passkey and the email / Ask
# Attnd preferences don't.
RISKY_ACCOUNT_ACTIONS = ('email', 'password', '2fa_disable', '2fa_codes', 'email_codes_on', 'email_codes_off',
                         'passkey_remove')


@signed_in
def account(request):
    """Change the email you sign in with, or your password. The account (and its events) stays the same."""
    email_form = EmailChangeForm(request.user, prefix='email')
    password_form = PasswordChangeForm(request.user, prefix='pw')
    recovery_codes = None
    action = request.POST.get('action') if request.method == 'POST' else None
    if action in RISKY_ACCOUNT_ACTIONS:  # a sign-in from the last 20 minutes first (console/recent_auth.py)
        from console import recent_auth
        if (response := recent_auth.require(request, reverse('console:account'))) is not None:
            return response
    if action in ('2fa_disable', '2fa_codes', 'email_codes_on', 'email_codes_off'):
        recovery_codes, response = account_two_factor_action(request)
        if response is not None:
            return response
    elif action in ('passkey_rename', 'passkey_remove'):
        return _passkey_action(request, action)
    elif action in ('session_signout', 'sessions_signout_others'):
        return _session_action(request, action)
    elif action == 'notify':  # stage I16: which organizer emails
        from console.models import ConsolePreference
        from console.organizer_mail import KINDS
        ConsolePreference.objects.update_or_create(user=request.user, defaults={
            'notify': {kind: request.POST.get(kind) == 'on' for kind in KINDS}})
        messages.success(request, 'Saved your email choices.')
        return redirect('console:account')
    elif action == 'forget_note':  # stage I11: what Ask Attnd remembers
        from console.models import ConsolePreference
        ConsolePreference.forget(request.user, request.POST.get('note', ''))
        messages.success(request, 'Ask Attnd won’t remember that any more.')
        return redirect('console:account')
    elif request.method == 'POST' and request.POST.get('action') == 'email':
        email_form = EmailChangeForm(request.user, request.POST, prefix='email')
        if email_form.is_valid():
            request.user.email = email_form.cleaned_data['email']
            request.user.save(update_fields=['email'])
            claim_invites(request.user)
            messages.success(request, f'You now sign in with {request.user.email}.')
            return redirect('console:account')
    elif request.method == 'POST' and request.POST.get('action') == 'password':
        password_form = PasswordChangeForm(request.user, request.POST, prefix='pw')
        if password_form.is_valid():
            user = password_form.save()
            update_session_auth_hash(request, user)
            messages.success(request, 'Password changed.')
            return redirect('console:account')
    user = request.user
    device = twofactor.confirmed_device(user)
    keys = list(Passkey.objects.filter(user=user))
    required = twofactor.is_required(user)
    for key in keys:
        key.removable = passkeys.can_remove(user, key)
    from console.models import ConsolePreference
    from events.models import Event
    pref = ConsolePreference.objects.filter(user=user).first()
    notes = list(reversed(pref.agent_notes)) if pref else []
    names = dict(Event.objects.filter(pk__in=[n['event_id'] for n in notes if n.get('event_id')])
                 .values_list('pk', 'name'))
    for note in notes:
        note['event'] = names.get(note.get('event_id'), '')
    from console.organizer_mail import KINDS
    chosen = pref.notify if pref else {}
    from console import user_sessions
    sessions = [{'id': r.pk, 'label': user_sessions.describe(r.user_agent), 'last_active': r.last_active,
                 'created_at': r.created_at, 'current': r.session_key == request.session.session_key}
                for r in user_sessions.live_sessions(user)]
    sessions.sort(key=lambda s: (not s['current'], -s['last_active'].timestamp()))
    return render(request, 'console/account.html', {
        'sign_in_sessions': sessions, 'other_sessions': any(not s['current'] for s in sessions),
        'agent_notes': notes,
        'notify_kinds': [{'key': k, 'label': v, 'on': chosen.get(k, True)} for k, v in KINDS.items()],
        'email_form': email_form, 'password_form': password_form, 'section': 'account',
        'two_factor': device, 'two_factor_required': required,
        'recovery_codes': recovery_codes,
        'recovery_codes_left': twofactor.recovery_codes_left(user),
        'passkeys': keys, 'passkey_name': webauthn.default_name(request.META.get('HTTP_USER_AGENT', '')),
        'has_second_factor': twofactor.has_second_factor(user),
        'email_codes_on': twofactor.email_codes_on(user), 'email_available': email_enabled() and bool(user.email),
        'totp_removable': device is not None and (not required or bool(keys)),
    })


def _session_action(request, action):
    """Sign out one of your other sessions, or every other one (console/user_sessions.py)."""
    from console import user_sessions
    current = request.session.session_key
    rows = [r for r in user_sessions.live_sessions(request.user) if r.session_key != current]
    if action == 'session_signout':
        rows = [r for r in rows if str(r.pk) == request.POST.get('session')]
    n = user_sessions.sign_out(request.user, rows)
    messages.success(request, 'Signed out that device.' if action == 'session_signout' and n else
                     f'Signed out {n} other device{"s" if n != 1 else ""}.' if n else 'No other device was signed in.')
    return redirect(reverse('console:account') + '#sessions')


def _passkey_action(request, action):
    """Rename or remove one of your passkeys. Staff can't remove their last strong method."""
    passkey = Passkey.objects.filter(user=request.user, pk=request.POST.get('passkey') or 0).first()
    if passkey is None:
        messages.error(request, 'That passkey is gone already.')
    elif action == 'passkey_rename':
        passkey.name = passkeys.clean_name(request.POST.get('name', ''), request.META.get('HTTP_USER_AGENT', ''))
        passkey.save(update_fields=['name'])
        messages.success(request, f'Renamed to “{passkey.name}”.')
    elif not passkeys.can_remove(request.user, passkey):
        messages.error(request, 'Admin accounts need a passkey or an authenticator app. '
                                'Add another one before removing this passkey.')
    else:
        passkey.delete()
        twofactor.after_factor_removed(request.user)
        messages.success(request, f'Passkey “{passkey.name}” removed. Also delete it from the device or '
                                  'password manager that holds it.')
    return redirect('console:account')
