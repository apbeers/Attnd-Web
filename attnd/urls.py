from django.contrib import admin
from django.contrib.sitemaps.views import sitemap
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static

from home.sitemaps import StaticSitemap
from join import views as join_views

sitemaps = {
    'static': StaticSitemap,
}

urlpatterns = [
    path('sitemap.xml', sitemap, {'sitemaps': sitemaps}, name='django.contrib.sitemaps.views.sitemap'),
    path('', include('home.urls'), name='home'),
    path('admin/', admin.site.urls),
    path('console/', include('console.urls'), name='console'),
    path('api/v1/events/', include('events.urls'), name='events'),
    path('api/v1/attest/', include('attestation.urls'), name='attestation'),
    path('join/', include('join.urls')),
    path('scrape/', include('scrape.urls')),
    path('accounts/', include('django.contrib.auth.urls')),
    path('.well-known/apple-app-site-association', join_views.AppleAppSiteAssociation.as_view(), name='aasa'),
    path('.well-known/assetlinks.json', join_views.AndroidAssetLinks.as_view(), name='android_asset_links'),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT) + static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
