"""A small Stripe API client over urllib (no `stripe` package).

Why not the official SDK (CLAUDE.md names Stripe as the expected exception to "no third-party libraries"): we call
about ten endpoints (accounts, account links, login links, Checkout Sessions, PaymentIntents, refunds, transfer
reversals, payouts, balance) with form-encoded POSTs and GETs, plus the webhook signature check (HMAC-SHA256). That is
~150 lines with the standard library, has no transitive dependencies, pins the API version explicitly, and makes the
tests' "Stripe mocked at the HTTP layer" trivial (everything goes through `_send`). If we ever need Stripe's typed
objects, retries with jitter or newer features (Terminal, Issuing), switching to the SDK is contained in this file.

    stripe_api.post('/v1/checkout/sessions', {...}, idempotency_key='order-…')
    stripe_api.get('/v1/accounts/acct_123')
    stripe_api.post('/v1/payouts', {...}, account='acct_123')   # on a connected account (Stripe-Account header)
"""

import hashlib
import hmac
import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request

from django.conf import settings

logger = logging.getLogger(__name__)

API_BASE = 'https://api.stripe.com'


class StripeError(Exception):
    def __init__(self, message, status=None, code='', type_='', payload=None):
        super().__init__(message)
        self.status, self.code, self.type, self.payload = status, code, type_, payload or {}


class NotConfigured(StripeError):
    pass


def configured():
    return bool(settings.STRIPE_SECRET_KEY)


def test_mode():
    return settings.STRIPE_SECRET_KEY.startswith(('sk_test_', 'rk_test_'))


def livemode():
    return configured() and not test_mode()


def encode(params, prefix=''):
    """Stripe's form encoding: nested dicts → a[b][c]=v, lists → a[0][b]=v, booleans → true/false, None dropped."""
    pairs = []
    items = params.items() if isinstance(params, dict) else enumerate(params)
    for key, value in items:
        name = f'{prefix}[{key}]' if prefix else str(key)
        if value is None:
            continue
        if isinstance(value, (dict, list, tuple)):
            pairs.extend(encode(value, name))
        elif isinstance(value, bool):
            pairs.append((name, 'true' if value else 'false'))
        else:
            pairs.append((name, str(value)))
    return pairs


def _send(method, url, data, headers, timeout):
    """One HTTP exchange → (status, body bytes). Tests replace this (tickets.tests_support.FakeStripe)."""
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def request(method, path, params=None, *, account=None, idempotency_key=None, timeout=20):
    if not configured():
        raise NotConfigured('Stripe is not set up (STRIPE_SECRET_KEY)')
    headers = {'Authorization': f'Bearer {settings.STRIPE_SECRET_KEY}',
               'User-Agent': 'Attnd/1.0 (+https://attnd.app)'}
    if settings.STRIPE_API_VERSION:
        headers['Stripe-Version'] = settings.STRIPE_API_VERSION
    if account:
        headers['Stripe-Account'] = account
    body = urllib.parse.urlencode(encode(params or {}))
    url = API_BASE + path
    data = None
    if method == 'GET':
        if body:
            url += '?' + body
    else:
        data = body.encode()
        headers['Content-Type'] = 'application/x-www-form-urlencoded'
        if idempotency_key:
            headers['Idempotency-Key'] = idempotency_key[:255]
    attempts = 2 if (method == 'GET' or idempotency_key) else 1
    last = None
    for attempt in range(attempts):
        try:
            status, raw = _send(method, url, data, headers, timeout)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last = StripeError(f'Could not reach Stripe: {exc}')
            if attempt + 1 < attempts and not getattr(settings, 'TESTING', False):
                time.sleep(0.5 * (attempt + 1))
            continue
        try:
            payload = json.loads(raw or b'{}')
        except ValueError:
            payload = {}
        if 200 <= status < 300:
            return payload
        error = payload.get('error') or {}
        last = StripeError(error.get('message') or f'Stripe answered {status}', status=status,
                           code=error.get('code', ''), type_=error.get('type', ''), payload=payload)
        if status < 500 and status != 429:
            break
    logger.warning('Stripe %s %s failed: %s', method, path, last)
    raise last


def get(path, params=None, **kwargs):
    return request('GET', path, params, **kwargs)


def post(path, params=None, **kwargs):
    return request('POST', path, params, **kwargs)


# ---------------------------------------------------------------------------
# Webhooks
# ---------------------------------------------------------------------------

class SignatureError(Exception):
    pass


def webhook_secrets():
    return [s for s in (settings.STRIPE_WEBHOOK_SECRET, settings.STRIPE_CONNECT_WEBHOOK_SECRET) if s]


def sign_payload(payload, secret, timestamp=None):
    """The Stripe-Signature header for `payload` (tests, and documenting the scheme)."""
    timestamp = int(timestamp or time.time())
    mac = hmac.new(secret.encode(), f'{timestamp}.'.encode() + payload, hashlib.sha256).hexdigest()
    return f't={timestamp},v1={mac}'


def verify_webhook(payload, header, secrets=None, tolerance=300, now=None):
    """The event (dict) if `header` (Stripe-Signature) signs `payload` with one of the secrets, within `tolerance` s."""
    secrets = webhook_secrets() if secrets is None else secrets
    if not secrets:
        raise SignatureError('No webhook secret configured')
    parts = {}
    for item in (header or '').split(','):
        k, _, v = item.strip().partition('=')
        parts.setdefault(k, []).append(v)
    try:
        timestamp = int(parts.get('t', [''])[0])
    except ValueError:
        raise SignatureError('No timestamp')
    if abs((now or time.time()) - timestamp) > tolerance:
        raise SignatureError('Timestamp outside the tolerance')
    signed = f'{timestamp}.'.encode() + payload
    expected = [hmac.new(s.encode(), signed, hashlib.sha256).hexdigest() for s in secrets]
    if not any(hmac.compare_digest(e, v) for e in expected for v in parts.get('v1', [])):
        raise SignatureError('Signature mismatch')
    try:
        return json.loads(payload)
    except ValueError:
        raise SignatureError('Not JSON')
