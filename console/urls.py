from django.urls import path
from console import views

urlpatterns = [
    path('', views.home, name='home'),
    path('signup/', views.SignUpView.as_view(), name='signup'),
    path('console/', views.EventView.as_view(), name='console'),
    path('console/<int:event_id>', views.EventView.as_view(), name='event'),
    path('console/<int:event_id>/locations', views.LocationsView.as_view(), name='locations'),
    path('console/<int:event_id>/notifications', views.NotificationsView.as_view(), name='notifications')
]
