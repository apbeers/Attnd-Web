from django.conf import settings


def maps(request):
    """Google Maps browser key (restricted to our domains) for console pages; empty = no Google Maps."""
    return {'GOOGLE_MAPS_BROWSER_KEY': settings.GOOGLE_MAPS_BROWSER_KEY, 'GOOGLE_MAPS_MAP_ID': settings.GOOGLE_MAPS_MAP_ID}
