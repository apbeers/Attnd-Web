from django.conf import settings


def maps(request):
    """Google Maps browser key (restricted to our domains) for console pages; empty = no Google Maps."""
    return {'GOOGLE_MAPS_BROWSER_KEY': settings.GOOGLE_MAPS_BROWSER_KEY, 'GOOGLE_MAPS_MAP_ID': settings.GOOGLE_MAPS_MAP_ID}


def store_links(request):
    """The apps' store pages (App Store / Google Play), for download links on any page."""
    return {'IOS_APP_STORE_URL': settings.IOS_APP_STORE_URL, 'ANDROID_PLAY_STORE_URL': settings.ANDROID_PLAY_STORE_URL}
