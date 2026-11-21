from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.contrib.sitemaps.views import sitemap
from django.urls import include, path, re_path
from django.conf import settings
from django.conf.urls.static import static

from attnd import views as attnd_views
from attnd.public import mark, public, public_include
from console import views_auth, views_passkeys
from events.app_status import app_status_view, retired_api_view
from home.sitemaps import StaticSitemap
from join import views as join_views

sitemaps = {
    'static': StaticSitemap,
}

urlpatterns = [
    path('health/', public(attnd_views.health), name='health'),
    path('sitemap.xml', public(sitemap), {'sitemaps': sitemaps}, name='django.contrib.sitemaps.views.sitemap'),
    path('', public_include('home.urls'), name='home'),
    # The admin lives at ADMIN_URL_PATH (/admin/ is a 404). Its login page goes to the site's sign-in,
    # which is rate limited and leads to the two-factor step.
    path(settings.ADMIN_URL_PATH + 'login/', public(views_auth.admin_login)),
    path(settings.ADMIN_URL_PATH, admin.site.urls),
    path('csp-report/', public(attnd_views.csp_report), name='csp_report'),
    path('console/diagnostics/', include('diagnostics.urls')),  # staff only (API-013)
    path('console/ai-spend/', include('credit.urls')),  # staff only: AI spend, limits, overrides (credit/)
    path('console/prospects/', include('prospects.urls')),  # staff only: the sales pipeline
    path('console/agent/', include('agent.urls')),  # Ask Attnd, the console's AI agent (signed-in organizers)
    path('console/', include('studio.urls')),  # Attnd Studio editor + staff pages (beta users only, else 404)
    path('console/', include('console.urls'), name='console'),
    path('api/v2/', public_include('events.urls_v2')),  # API keys / attestation, not sessions
    path('api/app-status/', public(app_status_view), name='app_status'),
    path('api/diagnostics/', public_include('diagnostics.urls_api')),  # CI symbol uploads, iOS symbolication (API-013)
    path('api/v1/attest/', public_include('attestation.urls'), name='attestation'),
    # Any other /api/vN/ path (v1's events API since Nov 19, 2026): 410 + update_required for retired
    # versions, JSON 404 otherwise.
    re_path(r'^api/v(?P<version>\d+)/', public(retired_api_view)),
    path('join/', public_include('join.urls')),
    # Shared plan links (API-015), with or without the trailing slash (the apps write them without).
    path('p/<str:event_code>/<str:payload>/qr.svg', public(join_views.shared_plan_qr), name='shared_plan_qr'),
    path('p/<str:event_code>/<str:payload>/', public(join_views.SharedPlanRedirect.as_view()), name='shared_plan'),
    path('p/<str:event_code>/<str:payload>', public(join_views.SharedPlanRedirect.as_view())),
    path('accounts/login/', public(views_auth.SignInView.as_view()), name='login'),
    path('accounts/2fa/', views_auth.two_factor_verify, name='two_factor_verify'),
    path('accounts/2fa/setup/', views_auth.two_factor_setup, name='two_factor_setup'),
    path('accounts/confirm/', views_auth.confirm_identity, name='confirm_identity'),  # recent sign-in check
    path('accounts/passkeys/auth/options/', public(views_passkeys.auth_options), name='passkey_auth_options'),
    path('accounts/passkeys/auth/', public(views_passkeys.auth_verify), name='passkey_auth'),
    # (the passkey registration endpoints answer their own JSON 403 when signed out)
    path('accounts/passkeys/register/options/', public(views_passkeys.register_options), name='passkey_register_options'),
    path('accounts/passkeys/register/', public(views_passkeys.register_verify), name='passkey_register'),
    path('accounts/passkeys/prompt/dismiss/', views_passkeys.prompt_dismiss, name='passkey_prompt_dismiss'),
    # Password reset in the console's style (the admin app's own templates would win otherwise).
    path('accounts/password_reset/', public(views_auth.ResetPasswordView.as_view(
        template_name='console/password_reset_form.html',
        email_template_name='console/password_reset_email.html',
        subject_template_name='console/password_reset_subject.txt')), name='password_reset'),
    path('accounts/password_reset/done/', auth_views.PasswordResetDoneView.as_view(
        template_name='console/password_reset_done.html'), name='password_reset_done'),
    path('accounts/reset/<uidb64>/<token>/', auth_views.PasswordResetConfirmView.as_view(
        template_name='console/password_reset_confirm.html'), name='password_reset_confirm'),
    path('accounts/reset/done/', auth_views.PasswordResetCompleteView.as_view(
        template_name='console/password_reset_complete.html'), name='password_reset_complete'),
    path('accounts/', include('django.contrib.auth.urls')),
    path('.well-known/apple-app-site-association', public(join_views.AppleAppSiteAssociation.as_view()), name='aasa'),
    path('.well-known/assetlinks.json', public(join_views.AndroidAssetLinks.as_view()), name='android_asset_links'),
] + mark(static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT) + static(settings.STATIC_URL, document_root=settings.STATIC_ROOT))
