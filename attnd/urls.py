from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.contrib.sitemaps.views import sitemap
from django.urls import include, path, re_path
from django.conf import settings
from django.conf.urls.static import static

from attnd import views as attnd_views
from events.app_status import app_status_view, retired_api_view
from home.sitemaps import StaticSitemap
from join import views as join_views

sitemaps = {
    'static': StaticSitemap,
}

urlpatterns = [
    path('health/', attnd_views.health, name='health'),
    path('sitemap.xml', sitemap, {'sitemaps': sitemaps}, name='django.contrib.sitemaps.views.sitemap'),
    path('', include('home.urls'), name='home'),
    path('admin/', admin.site.urls),
    path('console/', include('console.urls'), name='console'),
    path('api/v1/events/', include('events.urls'), name='events'),
    path('api/v2/', include('events.urls_v2')),
    path('api/app-status/', app_status_view, name='app_status'),
    path('api/v1/attest/', include('attestation.urls'), name='attestation'),
    # Any other /api/vN/ path: 410 + update_required for retired versions, JSON 404 otherwise.
    re_path(r'^api/v(?P<version>\d+)/', retired_api_view),
    path('join/', include('join.urls')),
    path('scrape/', include('scrape.urls')),
    # Password reset in the console's style (the admin app's own templates would win otherwise).
    path('accounts/password_reset/', auth_views.PasswordResetView.as_view(
        template_name='console/password_reset_form.html',
        email_template_name='console/password_reset_email.html',
        subject_template_name='console/password_reset_subject.txt'), name='password_reset'),
    path('accounts/password_reset/done/', auth_views.PasswordResetDoneView.as_view(
        template_name='console/password_reset_done.html'), name='password_reset_done'),
    path('accounts/reset/<uidb64>/<token>/', auth_views.PasswordResetConfirmView.as_view(
        template_name='console/password_reset_confirm.html'), name='password_reset_confirm'),
    path('accounts/reset/done/', auth_views.PasswordResetCompleteView.as_view(
        template_name='console/password_reset_complete.html'), name='password_reset_complete'),
    path('accounts/', include('django.contrib.auth.urls')),
    path('.well-known/apple-app-site-association', join_views.AppleAppSiteAssociation.as_view(), name='aasa'),
    path('.well-known/assetlinks.json', join_views.AndroidAssetLinks.as_view(), name='android_asset_links'),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT) + static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
