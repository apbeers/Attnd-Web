"""Sign-in, password reset and two-factor views (rate limited; see attnd/ratelimit.py and twofactor.py).
Passkey (WebAuthn) endpoints are in views_passkeys.py."""

from urllib.parse import urlencode

import qrcode
import qrcode.image.svg
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.safestring import mark_safe

from attnd.ratelimit import RateLimit, client_ip
from console import totp, twofactor, webauthn
from console.mail import email_enabled
from console.models import SignInSecurity, TwoFactorDevice

LOGIN_EMAIL = RateLimit('login_email')
LOGIN_IP = RateLimit('login_ip')
RESET_EMAIL = RateLimit('reset_email')
RESET_IP = RateLimit('reset_ip')
SIGNUP_IP = RateLimit('signup_ip')
OTP_USER = RateLimit('otp_user')
EMAIL_CODE_USER = RateLimit('email_code_user')

PASSKEY_PROMPT = '_passkey_prompt'  # session: offer "Sign in faster with a passkey" after a password sign-in

# Same wording whether or not the email has an account.
LOGIN_LIMITED = 'Too many sign-in attempts. Please wait 15 minutes and try again, or reset your password.'
RESET_LIMITED = 'Too many reset requests. Please wait an hour and try again.'
SIGNUP_LIMITED = 'Too many sign-up attempts from your network. Please wait an hour and try again.'
OTP_LIMITED = 'Too many wrong codes. Please wait 15 minutes and try again.'
EMAIL_LIMITED = 'We’ve sent several codes already. Please wait 15 minutes, or use another way to sign in.'


class SignInView(auth_views.LoginView):
    """Django's LoginView, with failed attempts limited per email and per IP."""

    def post(self, request, *args, **kwargs):
        self.ip = client_ip(request)
        self.email = request.POST.get('username', '').strip()
        if LOGIN_IP.exceeded(self.ip) or (self.email and LOGIN_EMAIL.exceeded(self.email)):
            context = self.get_context_data(form=self.get_form_class()(request), rate_limited=LOGIN_LIMITED)
            return self.render_to_response(context, status=429)
        return super().post(request, *args, **kwargs)

    def form_invalid(self, form):
        LOGIN_IP.hit(self.ip)
        if self.email:
            LOGIN_EMAIL.hit(self.email)
        return super().form_invalid(form)

    def form_valid(self, form):
        LOGIN_EMAIL.reset(self.email)
        response = super().form_valid(form)
        user = form.get_user()
        if not twofactor.has_passkeys(user) and not twofactor.security(user).passkey_prompt_dismissed:
            self.request.session[PASSKEY_PROMPT] = True  # shown once in the console (context processor)
        return response


class ResetPasswordView(auth_views.PasswordResetView):
    """Django's PasswordResetView, with requests limited per email and per IP."""

    def form_valid(self, form):
        ip, email = client_ip(self.request), form.cleaned_data['email']
        if RESET_IP.exceeded(ip) or RESET_EMAIL.exceeded(email):
            return self.render_to_response(self.get_context_data(form=form, rate_limited=RESET_LIMITED), status=429)
        RESET_IP.hit(ip)
        RESET_EMAIL.hit(email)
        return super().form_valid(form)


def signup_limited(request) -> bool:
    """Counts one signup attempt from this IP; True when over the limit (the caller answers 429)."""
    ip = client_ip(request)
    if SIGNUP_IP.exceeded(ip):
        return True
    SIGNUP_IP.hit(ip)
    return False


def admin_login(request):
    """The admin's own login page would skip the rate limits; use the site's sign-in instead."""
    if request.user.is_authenticated:  # signed in but not staff: the admin isn't for you
        return redirect('console:dashboard')
    return redirect(reverse('login') + '?' + urlencode({'next': request.GET.get('next') or reverse('admin:index')}))


def _next_url(request, target: str | None = None) -> str:
    """`target` (or the request's `next`) if it is a safe URL on this site, else the console."""
    if target is None:
        target = request.POST.get('next') or request.GET.get('next') or ''
    if target and url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()},
                                                  require_https=request.is_secure()):
        return target
    return settings.LOGIN_REDIRECT_URL


def _with_next(name: str, next_url: str) -> str:
    return reverse(name) + '?' + urlencode({'next': next_url})


@login_required
def two_factor_verify(request):
    """Second sign-in step after the password: a passkey (JavaScript, views_passkeys), or a code from the
    authenticator app, an emailed code, or a recovery code (this form)."""
    user = request.user
    next_url = _next_url(request)
    step = twofactor.pending_step(request)
    if step == 'setup':
        return redirect(_with_next('two_factor_setup', next_url))
    if step is None:
        return redirect(next_url)
    email_on = twofactor.email_codes_on(user)
    error, status, email_sent = None, 200, False
    if request.method == 'POST' and request.POST.get('action') == 'send_email':
        if not email_on:
            return redirect(_with_next('two_factor_verify', next_url))
        if EMAIL_CODE_USER.exceeded(user.pk):
            error, status = EMAIL_LIMITED, 429
        else:
            EMAIL_CODE_USER.hit(user.pk)
            twofactor.send_email_code(user)
            email_sent = True
    elif request.method == 'POST':
        if OTP_USER.exceeded(user.pk):
            error, status = OTP_LIMITED, 429
        else:
            used = twofactor.check_code(user, request.POST.get('code', ''))
            if used:
                OTP_USER.reset(user.pk)
                twofactor.mark_verified(request)
                if used == 'recovery':
                    left = twofactor.recovery_codes_left(user)
                    messages.warning(request, f'You used a recovery code; {left} left. '
                                              'You can make new ones on your Account page.')
                return redirect(next_url)
            OTP_USER.hit(user.pk)
            error = 'That code didn’t work. Check it and try again, or use a recovery code.'
    has_totp = twofactor.confirmed_device(user) is not None
    has_recovery = twofactor.recovery_codes_left(user) > 0
    kinds = [kind for kind, on in (('the 6-digit code from your authenticator app', has_totp),
                                   ('the code we email you', email_on),
                                   ('one of your recovery codes', has_recovery)) if on]
    code_help = ''
    if kinds:
        code_help = 'Enter ' + (kinds[0] if len(kinds) == 1 else ', '.join(kinds[:-1]) + ' or ' + kinds[-1]) + '.'
    return render(request, 'console/two_factor_verify.html', {
        'code_help': code_help,
        'next': next_url, 'error': error, 'email_sent': email_sent,
        'has_passkeys': twofactor.has_passkeys(user), 'has_totp': has_totp, 'email_on': email_on,
        'email_minutes': twofactor.EMAIL_CODE_MINUTES,
    }, status=status)


def _qr_svg(text: str) -> str:
    image = qrcode.make(text, image_factory=qrcode.image.svg.SvgPathImage, box_size=8, border=2)
    return image.to_string(encoding='unicode')


@login_required
def two_factor_setup(request):
    """Add a strong sign-in method. Staff without one are sent here until they've done it: a passkey first
    (JavaScript, views_passkeys), or an authenticator app (open the setup link, scan the QR code or type
    the key, confirm with a code). No recovery codes here: they are optional, from the Account page."""
    next_url = _next_url(request)
    if twofactor.pending_step(request) == 'verify':
        return redirect(_with_next('two_factor_verify', next_url))
    existing = TwoFactorDevice.objects.filter(user=request.user).first()
    if existing is not None and existing.confirmed:
        messages.info(request, 'Your authenticator app is already set up.')
        return redirect('console:account')
    device = existing or twofactor.start_setup(request.user)
    error, status = None, 200
    if request.method == 'POST':
        if OTP_USER.exceeded(request.user.pk):
            error, status = OTP_LIMITED, 429
        elif twofactor.confirm_setup(device, request.POST.get('code', '')):
            OTP_USER.reset(request.user.pk)
            twofactor.mark_verified(request)
            messages.success(request, 'Your authenticator app is on: after your password, enter the code it shows.')
            return redirect(next_url)
        else:
            OTP_USER.hit(request.user.pk)
            error = 'That code didn’t match. Enter the 6-digit code your app shows now.'
    account = request.user.email or request.user.username
    uri = totp.provisioning_uri(device.secret, account, settings.TWO_FACTOR_ISSUER)
    required = twofactor.is_required(request.user) and not twofactor.strong_methods(request.user)
    return render(request, 'console/two_factor_setup.html', {
        'next': next_url, 'error': error, 'otpauth_uri': uri, 'qr_svg': mark_safe(_qr_svg(uri)),
        'secret_groups': ' '.join(device.secret[i:i + 4] for i in range(0, len(device.secret), 4)),
        'required': required, 'offer_passkey': required,
        'passkey_name': webauthn.default_name(request.META.get('HTTP_USER_AGENT', '')),
    }, status=status)


def account_two_factor_action(request):
    """Account page actions for the authenticator app, emailed codes and recovery codes.
    Returns (recovery codes to show or None, response or None). Recovery codes ("Set up recovery codes" /
    "New recovery codes", action 2fa_codes) need a second factor and a fresh check: a code from the
    authenticator app or the account's password."""
    user = request.user
    action = request.POST.get('action')
    device = twofactor.confirmed_device(user)
    back = redirect('console:account')

    if action == 'email_codes_on':
        if twofactor.is_required(user):
            messages.error(request, 'Admin accounts use a passkey or an authenticator app instead.')
        elif not email_enabled() or not user.email:
            messages.error(request, 'Email isn’t set up on this server, so emailed codes are not available.')
        else:
            row = twofactor.security(user)
            row.email_codes_enabled = True
            row.save(update_fields=['email_codes_enabled'])
            twofactor.mark_verified(request)  # this session was fully signed in; keep it that way
            messages.success(request, 'Emailed codes are on: after your password, we’ll email you a code.')
        return None, back
    if action == 'email_codes_off':
        SignInSecurity.objects.filter(user=user).update(
            email_codes_enabled=False, email_code_hash='', email_code_expires_at=None)
        twofactor.after_factor_removed(user)
        messages.success(request, 'Emailed codes are off.')
        return None, back

    if OTP_USER.exceeded(user.pk):
        messages.error(request, OTP_LIMITED)
        return None, back
    if action == '2fa_codes':
        if not twofactor.has_second_factor(user):
            return None, back
        password = request.POST.get('password', '')
        ok = (twofactor.check_code(user, request.POST.get('code', ''), allow_recovery=False) is not None
              or (password and user.check_password(password)))
        if not ok:
            OTP_USER.hit(user.pk)
            messages.error(request, 'That didn’t work.')
            return None, back
        OTP_USER.reset(user.pk)
        return twofactor.new_recovery_codes(user), None
    if action == '2fa_disable':
        if device is None:
            return None, back
        if twofactor.is_required(user) and not twofactor.has_passkeys(user):
            messages.error(request, 'Admin accounts need a passkey or an authenticator app. '
                                    'Add a passkey before turning the authenticator app off.')
            return None, back
        if twofactor.check_totp(device, request.POST.get('code', '')) or \
                twofactor.use_recovery_code(user, request.POST.get('code', '')):
            OTP_USER.reset(user.pk)
            device.delete()
            twofactor.after_factor_removed(user)
            messages.success(request, 'The authenticator app is off.')
            return None, back
        OTP_USER.hit(user.pk)
        messages.error(request, 'That code didn’t work.')
    return None, back
