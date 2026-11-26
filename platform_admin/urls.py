from django.urls import path

from platform_admin import views, views_arch, views_email, views_gallery, views_mcp, views_styles

app_name = 'platform'
urlpatterns = [
    path('', views.home, name='home'),
    path('pricing/', views.save_pricing, name='pricing'),
    path('requests/approve/', views.approve_request, name='approve'),
    path('requests/decline/', views.decline_request, name='decline'),
    path('events/<int:event_id>/', views.event_detail, name='event'),
    path('events/<int:event_id>/save/', views.save_event, name='event_save'),
    path('events/<int:event_id>/payment/', views.record_payment, name='record_payment'),
    path('events/<int:event_id>/invoice/', views.send_invoice, name='send_invoice'),
    path('customers/<int:user_id>/', views.customer, name='customer'),
    path('customers.csv', views.customers_csv, name='customers_csv'),
    path('architecture/', views_arch.architecture, name='architecture'),
    path('app-options/', views_arch.app_options, name='app_options'),
    path('brand/', views_arch.brand, name='brand'),
    path('emails/', views_email.emails, name='emails'),
    path('connected-apps/', views_mcp.connected_apps, name='connected_apps'),
    path('connected-apps/revoke/', views_mcp.revoke_connected_app, name='connected_app_revoke'),
    path('components/', views_gallery.components, name='components'),
    path('site-styles/', views_styles.site_styles, name='site_styles'),
]
