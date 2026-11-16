"""Proving an email address without an account: 6-digit codes by email, then a session (web) or a token (apps).

- Codes last 10 minutes, are single use, and allow 5 tries each; sending is rate limited per address and per IP,
  and wrong codes per address and per IP (settings.RATE_LIMITS tickets_*).
- Asking for a code always answers the same whether or not the address has tickets (no enumeration).
- Tokens are 32 random bytes; only their SHA-256 is stored. Buyer tokens see the tickets held by one email; door
  tokens let one email work the doors of the events it is door staff (or team) for.
"""

import hashlib
import hmac
import secrets
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from attnd.ratelimit import RateLimit
from tickets.models import AccessToken, Buyer, VerificationCode, normalize_email

CODE_MINUTES = 10
MAX_ATTEMPTS = 5


class CodeError(Exception):
    def __init__(self, message, status=400, code='invalid_code'):
        super().__init__(message)
        self.status, self.code = status, code


def _hash(value):
    return hashlib.sha256(value.encode()).hexdigest()


def code_hash(email, code):
    return hmac.new(settings.SECRET_KEY.encode(), f'{normalize_email(email)}:{code}'.encode(), 'sha256').hexdigest()


def is_test_address(email):
    domain = settings.TICKETS_TEST_EMAIL_DOMAIN
    return bool(domain) and normalize_email(email).endswith('@' + domain)


def valid_email(email):
    from django.core.validators import validate_email
    from django.core.exceptions import ValidationError
    try:
        validate_email(email)
    except ValidationError:
        return False
    return len(email) <= 254


def send_code(email, purpose, ip):
    """Email a code (always 'sent' to the caller, unless rate limited). Raises CodeError(429) when limited."""
    email = normalize_email(email)
    if not valid_email(email):
        raise CodeError('Enter a valid email address.', 400, 'invalid_email')
    per_email, per_ip = RateLimit('tickets_code_email'), RateLimit('tickets_code_ip')
    if per_email.exceeded(f'{purpose}:{email}') or per_ip.exceeded(ip):
        raise CodeError('Too many codes requested. Please wait a few minutes.', 429, 'rate_limited')
    per_email.hit(f'{purpose}:{email}')
    per_ip.hit(ip)
    code = f'{secrets.randbelow(1_000_000):06d}'
    VerificationCode.objects.filter(email=email, purpose=purpose, used=False).update(used=True)
    test = is_test_address(email)
    VerificationCode.objects.create(email=email, purpose=purpose, code_hash=code_hash(email, code),
                                    expires_at=timezone.now() + timedelta(minutes=CODE_MINUTES),
                                    test_code=code if test and settings.TICKETS_DEV_TOOLS else '')
    if not test:
        from tickets import emails
        emails.send_code(email, code, purpose)
    return True


def check_code(email, code, purpose, ip):
    """True or raises CodeError. Consumes the code."""
    email = normalize_email(email)
    code = ''.join(ch for ch in str(code or '') if ch.isdigit())
    per_email, per_ip = RateLimit('tickets_verify_email'), RateLimit('tickets_verify_ip')
    if per_email.exceeded(f'{purpose}:{email}') or per_ip.exceeded(ip):
        raise CodeError('Too many wrong codes. Please wait a few minutes and ask for a new one.', 429, 'rate_limited')
    record = (VerificationCode.objects.filter(email=email, purpose=purpose, used=False,
                                              expires_at__gt=timezone.now()).order_by('-created_at').first())
    if record is None or record.attempts >= MAX_ATTEMPTS or len(code) != 6 or not hmac.compare_digest(
            record.code_hash, code_hash(email, code)):
        per_email.hit(f'{purpose}:{email}')
        per_ip.hit(ip)
        if record is not None:
            record.attempts += 1
            record.used = record.attempts >= MAX_ATTEMPTS
            record.save(update_fields=['attempts', 'used'])
        raise CodeError('That code isn’t right, or it expired. Ask for a new one.')
    record.used = True
    record.save(update_fields=['used'])
    per_email.reset(f'{purpose}:{email}')
    buyer, _ = Buyer.objects.get_or_create(email=email)
    if buyer.verified_at is None or buyer.user_id is None:
        from django.contrib.auth.models import User
        buyer.verified_at = buyer.verified_at or timezone.now()
        buyer.user = buyer.user or User.objects.filter(email__iexact=email).first()  # optional Attnd account
        buyer.save()
    return True


def issue_token(email, kind, label=''):
    """(raw token, AccessToken). The raw value is only returned here."""
    raw = secrets.token_urlsafe(32)
    days = settings.TICKETS_BUYER_TOKEN_DAYS if kind == AccessToken.KIND_BUYER else settings.TICKETS_DOOR_TOKEN_DAYS
    token = AccessToken.objects.create(kind=kind, email=normalize_email(email), token_hash=_hash(raw),
                                       label=(label or '')[:120], expires_at=timezone.now() + timedelta(days=days))
    return raw, token


def resolve_token(raw, kind):
    if not raw or len(raw) > 200:
        return None
    token = AccessToken.objects.filter(token_hash=_hash(raw.strip()), kind=kind, revoked=False,
                                       expires_at__gt=timezone.now()).first()
    if token is not None and (token.last_used_at is None or timezone.now() - token.last_used_at > timedelta(minutes=5)):
        AccessToken.objects.filter(pk=token.pk).update(last_used_at=timezone.now())
    return token


def revoke_token(raw):
    return AccessToken.objects.filter(token_hash=_hash((raw or '').strip())).update(revoked=True)
