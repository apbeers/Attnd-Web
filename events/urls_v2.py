from django.urls import path

from diagnostics import api as diagnostics_api
from events import apis_v2

app_name = 'events_v2'
urlpatterns = [
    path('events/nearby/', apis_v2.NearbyV2.as_view(), name='nearby'),
    path('events/lookup/', apis_v2.LookupV2.as_view(), name='lookup'),
    path('events/<int:event_id>/bundle/', apis_v2.BundleV2.as_view(), name='bundle'),
    path('events/<int:event_id>/join/', apis_v2.JoinV2.as_view(), name='join'),
    path('events/<int:event_id>/changes/', apis_v2.ChangesV2.as_view(), name='changes'),  # API-026 preview phones
    path('events/<int:event_id>/leave/', apis_v2.LeaveV2.as_view(), name='leave'),
    path('events/<int:event_id>/usage/', apis_v2.UsageV2.as_view(), name='usage'),
    path('devices/', apis_v2.DeviceV2.as_view(), name='device'),
    # API-013: debug log uploads and anonymous crash/hang reports.
    path('diagnostics/logs/', diagnostics_api.DiagnosticLogsV2.as_view(), name='diagnostics_logs'),
    path('diagnostics/reports/', diagnostics_api.DiagnosticReportsV2.as_view(), name='diagnostics_reports'),
]
