"""Small fixed-window rate limits on Django's cache (settings.CACHES, see the note there).

    limit = RateLimit('login_email')        # (count, window seconds) from settings.RATE_LIMITS
    if limit.exceeded(email): ...           # already at the limit?
    limit.hit(email)                        # count one attempt
    limit.reset(email)                      # e.g. after a successful sign-in

Identifiers (emails, IPs, user ids) are hashed into the cache key, so no personal data sits in the
cache and any string is a valid key. A window starts with the first counted attempt.
"""

import hashlib

from django.conf import settings
from django.core.cache import cache


def client_ip(request) -> str:
    """The client's IP. Behind Heroku's router the right-most X-Forwarded-For entry is the address the
    router saw; anything to its left was sent by the client and can be forged."""
    if getattr(settings, 'RATE_LIMIT_TRUST_X_FORWARDED_FOR', True):
        forwarded = request.META.get('HTTP_X_FORWARDED_FOR', '')
        hops = [hop.strip() for hop in forwarded.split(',') if hop.strip()]
        if hops:
            return hops[-1]
    return request.META.get('REMOTE_ADDR') or 'unknown'


class RateLimit:
    def __init__(self, name: str):
        self.name = name

    @property
    def limit(self) -> int:
        return settings.RATE_LIMITS[self.name][0]

    @property
    def window(self) -> int:
        return settings.RATE_LIMITS[self.name][1]

    def key(self, ident) -> str:
        digest = hashlib.sha256(str(ident).strip().lower().encode()).hexdigest()[:32]
        return f'rl:{self.name}:{digest}'

    def count(self, ident) -> int:
        return cache.get(self.key(ident), 0)

    def exceeded(self, ident) -> bool:
        return self.count(ident) >= self.limit

    def hit(self, ident) -> int:
        """Count one attempt; returns the new count. add + incr is atomic in LocMemCache, and incr keeps
        the key's original expiry, so the window is fixed from the first attempt."""
        key = self.key(ident)
        if cache.add(key, 1, self.window):
            return 1
        try:
            return cache.incr(key)
        except ValueError:  # expired between add and incr
            cache.add(key, 1, self.window)
            return 1

    def reset(self, ident) -> None:
        cache.delete(self.key(ident))
