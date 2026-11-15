from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.contrib.sitemaps.views import sitemap
from django.urls import include, path, re_path
from django.conf import settings
from django.conf.urls.static import static

from attnd import views as attnd_views
from console import views_auth, views_passkeys
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
    # The admin lives at ADMIN_URL_PATH (/admin/ is a 404). Its login page goes to the site's sign-in,
    # which is rate limited and leads to the two-factor step.
    path(settings.ADMIN_URL_PATH + 'login/', views_auth.admin_login),
    path(settings.ADMIN_URL_PATH, admin.site.urls),
    path('csp-report/', attnd_views.csp_report, name='csp_report'),
    path('console/diagnostics/', include('diagnostics.urls')),  # staff only (API-013)
    path('console/', include('console.urls'), name='console'),
    path('api/v1/events/', include('events.urls'), name='events'),
    path('api/v2/', include('events.urls_v2')),
    path('api/app-status/', app_status_view, name='app_status'),
    path('api/diagnostics/', include('diagnostics.urls_api')),  # CI symbol uploads, iOS symbolication (API-013)
    path('api/v1/attest/', include('attestation.urls'), name='attestation'),
    # Any other /api/vN/ path: 410 + update_required for retired versions, JSON 404 otherwise.
    re_path(r'^api/v(?P<version>\d+)/', retired_api_view),
    path('join/', include('join.urls')),
    # Shared plan links (API-015), with or without the trailing slash (the apps write them without).
    path('p/<str:event_code>/<str:payload>/qr.svg', join_views.shared_plan_qr, name='shared_plan_qr'),
    path('p/<str:event_code>/<str:payload>/', join_views.SharedPlanRedirect.as_view(), name='shared_plan'),
    path('p/<str:event_code>/<str:payload>', join_views.SharedPlanRedirect.as_view()),
    path('scrape/', include('scrape.urls')),
    path('accounts/login/', views_auth.SignInView.as_view(), name='login'),
    path('accounts/2fa/', views_auth.two_factor_verify, name='two_factor_verify'),
    path('accounts/2fa/setup/', views_auth.two_factor_setup, name='two_factor_setup'),
    path('accounts/2fa/recovery-codes/', views_auth.recovery_codes, name='two_factor_recovery_codes'),
    path('accounts/passkeys/auth/options/', views_passkeys.auth_options, name='passkey_auth_options'),
    path('accounts/passkeys/auth/', views_passkeys.auth_verify, name='passkey_auth'),
    path('accounts/passkeys/register/options/', views_passkeys.register_options, name='passkey_register_options'),
    path('accounts/passkeys/register/', views_passkeys.register_verify, name='passkey_register'),
    path('accounts/passkeys/prompt/dismiss/', views_passkeys.prompt_dismiss, name='passkey_prompt_dismiss'),
    # Password reset in the console's style (the admin app's own templates would win otherwise).
    path('accounts/password_reset/', views_auth.ResetPasswordView.as_view(
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
