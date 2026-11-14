from django.urls import path

from events import apis_v2

app_name = 'events_v2'
urlpatterns = [
    path('events/nearby/', apis_v2.NearbyV2.as_view(), name='nearby'),
    path('events/lookup/', apis_v2.LookupV2.as_view(), name='lookup'),
    path('events/<int:event_id>/bundle/', apis_v2.BundleV2.as_view(), name='bundle'),
    path('events/<int:event_id>/join/', apis_v2.JoinV2.as_view(), name='join'),
    path('events/<int:event_id>/leave/', apis_v2.LeaveV2.as_view(), name='leave'),
    path('events/<int:event_id>/usage/', apis_v2.UsageV2.as_view(), name='usage'),
    path('devices/', apis_v2.DeviceV2.as_view(), name='device'),
]
