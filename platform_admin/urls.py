from django.urls import path

from platform_admin import views, views_arch

app_name = 'platform'
urlpatterns = [
    path('', views.home, name='home'),
    path('pricing/', views.save_pricing, name='pricing'),
    path('requests/approve/', views.approve_request, name='approve'),
    path('requests/decline/', views.decline_request, name='decline'),
    path('events/<int:event_id>/', views.event_detail, name='event'),
    path('events/<int:event_id>/save/', views.save_event, name='event_save'),
    path('events/<int:event_id>/payment/', views.record_payment, name='record_payment'),
    path('customers/<int:user_id>/', views.customer, name='customer'),
    path('customers.csv', views.customers_csv, name='customers_csv'),
    path('architecture/', views_arch.architecture, name='architecture'),
    path('app-options/', views_arch.app_options, name='app_options'),
    path('brand/', views_arch.brand, name='brand'),
]
