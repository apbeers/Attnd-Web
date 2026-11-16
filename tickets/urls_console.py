from django.urls import path

from tickets import views_console as v, views_staff as s

app_name = 'tickets_console'
urlpatterns = [
    path('tickets/demo/', s.demo, name='demo'),  # staff walkthrough kit
    path('events/<int:event_id>/tickets/', v.home, name='home'),
    path('events/<int:event_id>/tickets/types/', v.types, name='types'),
    path('events/<int:event_id>/tickets/types/new/', v.type_edit, name='type_new'),
    path('events/<int:event_id>/tickets/types/<int:type_id>/', v.type_edit, name='type_edit'),
    path('events/<int:event_id>/tickets/promos/', v.promos, name='promos'),
    path('events/<int:event_id>/tickets/settings/', v.ticket_settings, name='settings'),
    path('events/<int:event_id>/tickets/draft/', v.draft, name='draft'),
    path('events/<int:event_id>/tickets/orders/', v.orders, name='orders'),
    path('events/<int:event_id>/tickets/orders/<int:order_id>/', v.order_detail, name='order'),
    path('events/<int:event_id>/tickets/comp/', v.comp, name='comp'),
    path('events/<int:event_id>/tickets/attendees.csv', v.attendees_csv, name='attendees_csv'),
    path('events/<int:event_id>/tickets/checkins/', v.checkins, name='checkins'),
    path('events/<int:event_id>/tickets/door/', v.door, name='door'),
    path('events/<int:event_id>/tickets/message/', v.message, name='message'),
    path('events/<int:event_id>/tickets/payouts/', v.payouts, name='payouts'),
    path('events/<int:event_id>/tickets/cancel/', v.cancel, name='cancel'),
]
