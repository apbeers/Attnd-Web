from django.conf import settings
from django.urls import path

from diagnostics import api as diagnostics_api
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
    # API-013: debug log uploads and anonymous crash/hang reports.
    path('diagnostics/logs/', diagnostics_api.DiagnosticLogsV2.as_view(), name='diagnostics_logs'),
    path('diagnostics/reports/', diagnostics_api.DiagnosticReportsV2.as_view(), name='diagnostics_reports'),
]

# API-017: tickets. "My tickets" (buyer email code → buyer token) and door mode (door token, offline lists).
# Parked: these endpoints exist only with TICKETS_ENABLED (API_V2.md → Tickets).
if settings.TICKETS_ENABLED:
    from tickets import api as tickets_api

    urlpatterns += [
        path('tickets/code/', tickets_api.SendCode.as_view(), name='tickets_code'),
        path('tickets/verify/', tickets_api.Verify.as_view(), name='tickets_verify'),
        path('tickets/mine/', tickets_api.MyTickets.as_view(), name='tickets_mine'),
        path('tickets/signout/', tickets_api.SignOut.as_view(), name='tickets_signout'),
        path('tickets/test-inbox/', tickets_api.TestInbox.as_view(), name='tickets_test_inbox'),
        path('tickets/test/order/', tickets_api.TestOrder.as_view(), name='tickets_test_order'),
        path('tickets/test/pay/', tickets_api.TestPay.as_view(), name='tickets_test_pay'),
        path('door/code/', tickets_api.DoorSendCode.as_view(), name='door_code'),
        path('door/verify/', tickets_api.DoorVerify.as_view(), name='door_verify'),
        path('door/signout/', tickets_api.DoorSignOut.as_view(), name='door_signout'),
        path('door/events/', tickets_api.DoorEvents.as_view(), name='door_events'),
        path('door/events/<int:event_id>/manifest/', tickets_api.DoorManifest.as_view(), name='door_manifest'),
        path('door/events/<int:event_id>/revocations/', tickets_api.DoorRevocations.as_view(), name='door_revocations'),
        path('door/events/<int:event_id>/checkins/', tickets_api.DoorCheckins.as_view(), name='door_checkins'),
        path('door/events/<int:event_id>/counts/', tickets_api.DoorCounts.as_view(), name='door_counts'),
    ]
