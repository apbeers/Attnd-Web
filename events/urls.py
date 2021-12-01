from django.urls import path
from events import apis

app_name = 'events'
urlpatterns = [
    path('<int:event_id>/locations/', apis.AllLocationsView.as_view(), name='get_all_locations'),
    path('<int:event_id>/notifications/', apis.AllNotificationsView.as_view(), name='get_all_notifications'),
    # path('<int:event_id>/locations/<int:location_id>/', apis.SingleLocationView.as_view(), name='get_single_location'),
    path('<str:event_id>/', apis.SingleEventView.as_view(), name='get_single_event'),
    # path('<int:event_id>/notifications/<int:notification_id>/', apis.SingleNotificationView.as_view(), name='get_single_notification'),
    path('nearby_events/<str:latitude>/<str:longitude>/', apis.NearbyEventsView.as_view(), name='get_nearby_events'),
    path('event_by_code_or_name/', apis.EventByCodeOrNameView.as_view(), name='get_event_by_code_or_name'),
    path('<int:event_id>/join/<str:device_id>/', apis.JoinEventView.as_view(), name='join_event'),
    path('<int:event_id>/leave/<str:device_id>/', apis.LeaveEventView.as_view(), name='leave_event'),
    path('pushnotifications/subscribe/<str:device_id>/', apis.PushNotificationsSubscribe.as_view(), name='push_notifications_subscribe'),
    path('pushnotifications/unsubscribe/<str:device_id>/', apis.PushNotificationsUnSubscribe.as_view(), name='push_notifications_unsubscribe')
]
