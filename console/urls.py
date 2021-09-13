from django.urls import path

from console import views

urlpatterns = [
    # Registration Views
    path('signup/', views.SignUpView.as_view(), name='signup'),

    # Event Views
    path('<int:event_id>/event/', views.EventView.as_view(), name='event'),
    path('<int:event_id>/update/', views.EventUpdateView.as_view(), name='event_update'),
    path('<int:event_id>/delete/', views.EventDeleteView.as_view(), name='event_delete'),
    path('<int:event_id>/create/', views.EventCreateView.as_view(), name='event_create'),

    # Location Views
    path('<int:event_id>/location/', views.LocationsView.as_view(), name='locations'),

    # Notification Views
    path('<int:event_id>/notifications/create/', views.NotificationCreateView.as_view(), name='notification_create'),
    path('<int:event_id>/notifications/list/', views.NotificationsListView.as_view(), name='notification_list'),
    path('<int:event_id>/notifications/update/<int:notification_id>/', views.NotificationUpdateView.as_view(), name='notification_update'),
    path('<int:event_id>/notifications/delete/<int:notification_id>/', views.NotificationDeleteView.as_view(), name='notification_delete'),

    # Analytics Views
    path('<int:event_id>/analytics/', views.AnalyticsView.as_view(), name='analytics'),

    # Billing Views
    path('<int:event_id>/billing/', views.BillingView.as_view(), name='billing'),
]
