from django.urls import path

from console import views

app_name = 'console'
urlpatterns = [
    # Registration Views
    path('signup/', views.SignUpView.as_view(), name='signup'),
    path('login/', views.LoginView.as_view(), name='login'),

    # Event Views
    path('', views.EventCreateView.as_view(), name='create_first_event'),
    path('create/', views.EventCreateView.as_view(), name='event_create'),
    path('<int:event_id>/', views.EventView.as_view(), name='event'),
    path('<int:event_id>/update/', views.EventUpdateView.as_view(), name='event_update'),
    path('<int:event_id>/delete/', views.EventDeleteView.as_view(), name='event_delete'),

    # Location Views
    path('<int:event_id>/locations/create/', views.LocationCreateView.as_view(), name='location_create'),
    path('<int:event_id>/locations/list/', views.LocationsListView.as_view(), name='location_list'),
    path('<int:event_id>/locations/update/<int:location_id>/', views.LocationUpdateView.as_view(), name='location_update'),
    path('<int:event_id>/locations/delete/<int:location_id>/', views.LocationDeleteView.as_view(), name='location_delete'),

    # Notification Views
    path('<int:event_id>/notifications/create/', views.NotificationCreateView.as_view(), name='notification_create'),
    path('<int:event_id>/notifications/list/', views.NotificationsListView.as_view(), name='notification_list'),
    path('<int:event_id>/notifications/update/<int:notification_id>/', views.NotificationUpdateView.as_view(), name='notification_update'),
    path('<int:event_id>/notifications/delete/<int:notification_id>/', views.NotificationDeleteView.as_view(), name='notification_delete'),

    # Analytics Views
    path('<int:event_id>/analytics/', views.AnalyticsView.as_view(), name='analytics'),

    # Billing Views
    path('<int:event_id>/billing/', views.BillingView.as_view(), name='billing'),
    path('<int:event_id>/<str:order_id>', views.ValidatePayPalTransactionView.as_view(), name='validate_paypal_transaction')
]
