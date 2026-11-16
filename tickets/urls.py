"""Public ticket routes on attnd.app (mounted at the site root in attnd/urls.py)."""
from django.urls import path, re_path

from tickets import views_public as v, views_wallet as w

app_name = 'tickets'
urlpatterns = [
    path('t/<str:event_code>/', v.ticket_page, name='page'),
    path('t/<str:event_code>/embed/', v.embed, name='embed'),
    path('t/<str:event_code>/waitlist/', v.join_waitlist, name='waitlist'),
    path('tickets/order/<uuid:public_id>/<str:key>/', v.order_page, name='order'),
    path('tickets/order/<uuid:public_id>/<str:key>/resend/', v.order_resend, name='order_resend'),
    path('tickets/order/<uuid:public_id>/<str:key>/refund/', v.order_refund, name='order_refund'),
    path('tickets/ticket/<uuid:public_id>/<str:key>/', v.ticket_page_view, name='ticket'),
    path('tickets/ticket/<uuid:public_id>/<str:key>/qr.svg', v.ticket_qr, name='ticket_qr'),
    path('tickets/ticket/<uuid:public_id>/<str:key>/wallet.pkpass', v.apple_pass, name='apple_pass'),
    path('tickets/ticket/<uuid:public_id>/<str:key>/google-wallet/', v.google_pass, name='google_pass'),
    path('tickets/lookup/', v.lookup, name='lookup'),
    path('tickets/mine/', v.mine, name='mine'),
    path('tickets/stripe/webhook/', v.stripe_webhook, name='stripe_webhook'),
    # Apple Wallet web service (webServiceURL = SITE_URL/wallet/)
    re_path(r'^wallet/v1/devices/(?P<device>[^/]+)/registrations/(?P<pass_type>[^/]+)/(?P<serial>[^/]+)$',
            w.registration, name='wallet_registration'),
    re_path(r'^wallet/v1/devices/(?P<device>[^/]+)/registrations/(?P<pass_type>[^/]+)$', w.serials,
            name='wallet_serials'),
    re_path(r'^wallet/v1/passes/(?P<pass_type>[^/]+)/(?P<serial>[^/]+)$', w.latest_pass, name='wallet_pass'),
    path('wallet/v1/log', w.log, name='wallet_log'),
]
