from django.contrib.sitemaps import Sitemap
from django.urls import reverse


class StaticSitemap(Sitemap):
    changefreq = 'monthly'
    priority = 0.8

    def items(self):
        return ['home:homepage', 'home:privacy']

    def location(self, item):
        return reverse(item)


class EventSitemap(Sitemap):
    """Join pages of events that are meant to be found: published, listed (public), paid; never test, showcase,
    demo or draft events (their join pages say noindex, join.views)."""
    changefreq = 'daily'
    priority = 0.5

    def items(self):
        from events.models import Event
        return (Event.objects.filter(status=Event.STATUS_PUBLISHED, public=True, paid=True, testing_only=False,
                                     showcase=False).order_by('pk'))

    def location(self, event):
        return reverse('join:join_event', args=[event.event_code])

    def lastmod(self, event):
        return event.content_updated_at


def indexable_event(event):
    from events.models import Event
    return (event.status == Event.STATUS_PUBLISHED and event.public and event.paid and not event.testing_only
            and not event.showcase)
