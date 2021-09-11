from django.urls import path
from events import apis

urlpatterns = [
    path('<int:event_id>/locations/', apis.AllLocationsView.as_view(), name='get_all_locations'),
    path('<int:event_id>/notifications/', apis.AllLocationsView.as_view(), name='get_all_notifications'),
    path('<int:event_id>/locations/<int:location_id>/', apis.SingleEventView.as_view(), name='get_single_location'),
    path('<str:event_code>/', apis.SingleEventView.as_view(), name='get_single_event'),
    path('nearby_events/<str:latitude>/<str:longitude>/', apis.NearbyEventsView.as_view(), name='get_nearby_events'),
    path('event_by_code_or_name/', apis.EventByCodeOrNameView.as_view(), name='get_event_by_code_or_name')
]
