from django import template

from tickets import money

register = template.Library()


@register.filter
def cents(value, currency='usd'):
    """1234 → $12.34"""
    return money.fmt(value or 0, currency or 'usd')


@register.filter
def dollars(value):
    """1234 → 12.34 (form fields)"""
    return money.dollars(value or 0)
