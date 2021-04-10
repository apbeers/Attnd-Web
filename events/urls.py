from django.urls import path
from events import apis

urlpatterns = [
    path('<int:event_id>/locations/', apis.get_all_locations, name='get_all_locations'),
    path('<int:event_id>/notifications/', apis.get_all_notifications, name='get_all_notifications'),
    path('<int:event_id>/locations/<int:location_id>/', apis.get_single_location, name='get_single_locations'),
    path('<str:event_code>/', apis.get_single_event, name='get_single_event'),
]
