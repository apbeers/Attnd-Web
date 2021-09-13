from django.urls import path
from console import views

urlpatterns = [
    path('signup/', views.SignUpView.as_view(), name='signup'),
    path('', views.EventView.as_view(), name='no_event_selected'),
    path('<int:event_id>/', views.EventView.as_view(), name='event'),
    path('<int:event_id>/location/', views.LocationsView.as_view(), name='locations'),
    path('<int:event_id>/notifications/create/', views.NotificationCreateView.as_view(), name='notification_create'),
    path('<int:event_id>/notifications/list/', views.NotificationsListView.as_view(), name='notification_list'),
    path('<int:event_id>/notifications/update/<int:notification_id>/', views.NotificationUpdateView.as_view(), name='notification_update'),
    path('<int:event_id>/notifications/delete/<int:notification_id>/', views.NotificationDeleteView.as_view(), name='notification_delete'),
    path('<int:event_id>/analytics/', views.AnalyticsView.as_view(), name='analytics'),
    path('<int:event_id>/billing/', views.BillingView.as_view(), name='billing'),
]
