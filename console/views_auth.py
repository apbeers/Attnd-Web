"""Sign-in, password reset and two-factor views (rate limited; see attnd/ratelimit.py and twofactor.py)."""

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
from console import totp, twofactor
from console.models import TwoFactorDevice

LOGIN_EMAIL = RateLimit('login_email')
LOGIN_IP = RateLimit('login_ip')
RESET_EMAIL = RateLimit('reset_email')
RESET_IP = RateLimit('reset_ip')
SIGNUP_IP = RateLimit('signup_ip')
OTP_USER = RateLimit('otp_user')

# Same wording whether or not the email has an account.
LOGIN_LIMITED = 'Too many sign-in attempts. Please wait 15 minutes and try again, or reset your password.'
RESET_LIMITED = 'Too many reset requests. Please wait an hour and try again.'
SIGNUP_LIMITED = 'Too many sign-up attempts from your network. Please wait an hour and try again.'
OTP_LIMITED = 'Too many wrong codes. Please wait 15 minutes and try again.'


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
        return super().form_valid(form)


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


def _next_url(request) -> str:
    target = request.POST.get('next') or request.GET.get('next') or ''
    if target and url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()},
                                                  require_https=request.is_secure()):
        return target
    return settings.LOGIN_REDIRECT_URL


@login_required
def two_factor_verify(request):
    """Second sign-in step: a code from the authenticator app, or a recovery code."""
    device = twofactor.confirmed_device(request.user)
    next_url = _next_url(request)
    if device is None:
        return redirect(reverse('two_factor_setup') + '?' + urlencode({'next': next_url}))
    if twofactor.is_verified(request):
        return redirect(next_url)
    error, status = None, 200
    if request.method == 'POST':
        if OTP_USER.exceeded(request.user.pk):
            error, status = OTP_LIMITED, 429
        else:
            used = twofactor.check_code(device, request.POST.get('code', ''))
            if used:
                OTP_USER.reset(request.user.pk)
                twofactor.mark_verified(request)
                if used == 'recovery':
                    left = len(device.recovery_codes)
                    messages.warning(request, f'You used a recovery code; {left} left. '
                                              'You can make new ones on your Account page.')
                return redirect(next_url)
            OTP_USER.hit(request.user.pk)
            error = 'That code didn’t work. Check the time on your phone, or use a recovery code.'
    return render(request, 'console/two_factor_verify.html', {'next': next_url, 'error': error}, status=status)


def _qr_svg(text: str) -> str:
    image = qrcode.make(text, image_factory=qrcode.image.svg.SvgPathImage, box_size=8, border=2)
    return image.to_string(encoding='unicode')


@login_required
def two_factor_setup(request):
    """Turn on two-factor sign-in: scan the QR code (or type the key), confirm with a code, then save the
    recovery codes, which are shown only this once. Staff are sent here until they've done it."""
    next_url = _next_url(request)
    existing = TwoFactorDevice.objects.filter(user=request.user).first()
    if existing is not None and existing.confirmed:
        if not twofactor.is_verified(request):
            return redirect(reverse('two_factor_verify') + '?' + urlencode({'next': next_url}))
        messages.info(request, 'Two-factor sign-in is already on.')
        return redirect('console:account')
    device = existing or twofactor.start_setup(request.user)
    error, status = None, 200
    if request.method == 'POST':
        if OTP_USER.exceeded(request.user.pk):
            error, status = OTP_LIMITED, 429
        else:
            codes = twofactor.confirm_setup(device, request.POST.get('code', ''))
            if codes:
                OTP_USER.reset(request.user.pk)
                twofactor.mark_verified(request)
                return render(request, 'console/two_factor_setup.html',
                              {'recovery_codes': codes, 'next': next_url})
            OTP_USER.hit(request.user.pk)
            error = 'That code didn’t match. Enter the 6-digit code your app shows now.'
    account = request.user.email or request.user.username
    uri = totp.provisioning_uri(device.secret, account, settings.TWO_FACTOR_ISSUER)
    return render(request, 'console/two_factor_setup.html', {
        'next': next_url, 'error': error, 'otpauth_uri': uri, 'qr_svg': mark_safe(_qr_svg(uri)),
        'secret_groups': ' '.join(device.secret[i:i + 4] for i in range(0, len(device.secret), 4)),
        'required': twofactor.is_required(request.user),
    }, status=status)


def account_two_factor_action(request):
    """Account page actions: new recovery codes, or turn two-factor off (not for staff). Both need a
    current code. Returns (recovery codes to show or None, response or None)."""
    device = twofactor.confirmed_device(request.user)
    action = request.POST.get('action')
    if device is None:
        return None, redirect('console:account')
    if OTP_USER.exceeded(request.user.pk):
        messages.error(request, OTP_LIMITED)
        return None, redirect('console:account')
    if action == '2fa_disable' and twofactor.is_required(request.user):
        messages.error(request, 'Admin accounts must keep two-factor sign-in on.')
        return None, redirect('console:account')
    used = twofactor.check_code(device, request.POST.get('code', ''), allow_recovery=(action == '2fa_disable'))
    if not used:
        OTP_USER.hit(request.user.pk)
        messages.error(request, 'That code didn’t work.')
        return None, redirect('console:account')
    OTP_USER.reset(request.user.pk)
    if action == '2fa_disable':
        device.delete()
        request.session.pop(twofactor.SESSION_KEY, None)
        messages.success(request, 'Two-factor sign-in is off.')
        return None, redirect('console:account')
    return twofactor.new_recovery_codes(device), None
