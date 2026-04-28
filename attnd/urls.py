from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static

from join import views as join_views

urlpatterns = [
    path('', include('home.urls'), name='home'),
    path('admin/', admin.site.urls),
    path('console/', include('console.urls'), name='console'),
    path('api/v1/events/', include('events.urls'), name='events'),
    path('join/', include('join.urls')),
    path('scrape/', include('scrape.urls')),
    path('accounts/', include('django.contrib.auth.urls')),
    path('.well-known/apple-app-site-association', join_views.AppleAppSiteAssociation.as_view(), name='aasa'),
    path('.well-known/assetlinks.json', join_views.AndroidAssetLinks.as_view(), name='android_asset_links'),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT) + static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
