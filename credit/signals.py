"""A visitor's AI spend (before an account) moves into the account's month when they sign up or sign in."""
from django.contrib.auth.signals import user_logged_in
from django.dispatch import receiver


@receiver(user_logged_in)
def _carry_visitor_spend(sender, request, user, **kwargs):
    if request is None or not hasattr(request, 'session'):
        return
    key = request.session.get('ai_visitor')
    if not key:
        return
    from credit.models import AiSpend
    AiSpend.objects.filter(visitor=key, user__isnull=True).update(user=user, visitor='')
    request.session.pop('ai_visitor', None)
