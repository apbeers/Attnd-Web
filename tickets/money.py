"""Prices, the buyer's processing fee and all-in prices (spec: Business rules).

Buyers pay the processing fee, so the organizer receives the full ticket price:

    total = (subtotal + fixed) / (1 - rate), rounded UP to the cent;  fee = total - subtotal

The fee is collected as the destination charge's application fee and pays the Stripe fee billed to the platform.
Free orders (subtotal 0) have no fee. The FTC rule on live-event tickets (May 2025) wants the total including
mandatory fees shown first, so every price display uses `all_in()` (the gross-up of that one ticket alone); the
order total is computed for the whole cart, so it is never more than the sum of the all-in prices shown (the fixed
part is paid once per order).
"""

from decimal import ROUND_CEILING, Decimal

from django.conf import settings


def rate():
    return Decimal(str(settings.TICKETS_FEE_PERCENT)) / 100


def fixed():
    return Decimal(settings.TICKETS_FEE_FIXED_CENTS)


def fee_for(subtotal_cents):
    """The processing fee in cents for an order whose ticket money is `subtotal_cents` (after discounts)."""
    subtotal_cents = int(subtotal_cents)
    if subtotal_cents <= 0:
        return 0
    total = ((Decimal(subtotal_cents) + fixed()) / (1 - rate())).to_integral_value(rounding=ROUND_CEILING)
    return int(total) - subtotal_cents


def total_for(subtotal_cents):
    return int(subtotal_cents) + fee_for(subtotal_cents) if subtotal_cents > 0 else 0


def all_in(price_cents):
    """The price to show for one ticket: its price plus the fee of buying it alone."""
    return total_for(price_cents)


def split_fee(fee_cents, amounts):
    """Share `fee_cents` across tickets in proportion to `amounts` (cents); the shares add up exactly."""
    total = sum(amounts)
    if not amounts:
        return []
    if total <= 0:
        return [0] * len(amounts)
    shares = [fee_cents * a // total for a in amounts]
    left = fee_cents - sum(shares)
    # Hand out the rounding remainder to the largest amounts first (stable by position).
    for i in sorted(range(len(amounts)), key=lambda i: (-amounts[i], i))[:left]:
        shares[i] += 1
    return shares


SYMBOLS = {'usd': '$', 'cad': 'CA$', 'eur': '€', 'gbp': '£', 'aud': 'A$'}


def fmt(cents, currency='usd'):
    """$27.50 (always two decimals; $0.00 for free)."""
    cents = int(cents or 0)
    sign = '-' if cents < 0 else ''
    cents = abs(cents)
    return f'{sign}{SYMBOLS.get((currency or "usd").lower(), "$")}{cents // 100:,}.{cents % 100:02d}'


def parse_dollars(text):
    """'25', '25.5', '$1,025.00' → cents. Raises ValueError on anything else (or more than 2 decimals)."""
    value = str(text or '').strip().replace('$', '').replace(',', '')
    if not value:
        raise ValueError('Enter an amount')
    amount = Decimal(value)
    if amount < 0 or amount != amount.quantize(Decimal('0.01')):
        raise ValueError('Enter dollars and cents, e.g. 25 or 25.50')
    return int(amount * 100)


def dollars(cents):
    """Cents → '25.00' (for form fields)."""
    cents = int(cents or 0)
    return f'{cents // 100}.{cents % 100:02d}'
