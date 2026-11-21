from django.contrib.auth.decorators import login_not_required
from django.urls import path

from agent import views as agent_views
from console import views, views_events as v, views_undo

app_name = 'console'
urlpatterns = [
    path('', v.dashboard, name='dashboard'),
    path('signup/', login_not_required(views.SignUpView.as_view()), name='signup'),
    path('account/', views.account, name='account'),
    path('tasks/', v.tasks_feed, name='tasks'),
    path('tasks/<uuid:job_id>/retry/', v.task_retry, name='task_retry'),
    path('chat-default/', v.chat_default, name='chat_default'),
    path('undo/last/', views_undo.undo_last, name='undo_last'),
    path('assistant/', agent_views.assistant_page, name='assistant'),
    path('undo/<int:undo_id>/', views_undo.undo_change, name='undo'),
    path('sw.js', v.service_worker, name='service_worker'),
    path('manifest.webmanifest', v.manifest, name='manifest'),
    path('new/', v.event_new, name='event_new'),
    path('jobs/<uuid:job_id>/', v.job_detail, name='job'),
    path('changes/<int:changeset_id>/', v.changeset_detail, name='changeset'),

    path('events/<int:event_id>/', v.event_overview, name='event'),
    path('events/<int:event_id>/edit/', v.event_edit, name='event_edit'),
    path('events/<int:event_id>/import/', v.event_import, name='event_import'),
    path('walkthrough/skip/', v.walkthrough_skip, name='walkthrough_skip'),
    path('events/<int:event_id>/duplicate/', v.event_duplicate, name='event_duplicate'),
    path('events/<int:event_id>/status/', v.event_status, name='event_status'),
    path('events/<int:event_id>/sources/', v.event_sources, name='event_sources'),
    path('events/<int:event_id>/refresh/', v.event_refresh, name='event_refresh'),
    path('events/<int:event_id>/delete/', v.event_delete, name='event_delete'),
    path('events/<int:event_id>/qr.png', v.event_qr, name='event_qr'),
    path('events/<int:event_id>/preview.png', v.event_preview_qr, name='event_preview_qr'),
    path('events/<int:event_id>/phones/', v.event_phones, name='event_phones'),
    path('events/<int:event_id>/preview/remove/', v.event_preview_remove, name='event_preview_remove'),
    path('events/<int:event_id>/poster/', v.event_poster, name='event_poster'),
    path('events/<int:event_id>/export/<slug:kind>.csv', v.export_csv, name='export'),

    path('events/<int:event_id>/locations/', v.locations, name='locations'),
    path('events/<int:event_id>/locations/new/', v.location_edit, name='location_new'),
    path('events/<int:event_id>/locations/fill/', v.location_fill, name='location_fill'),
    path('events/<int:event_id>/places/', v.place_search, name='place_search'),
    path('events/<int:event_id>/locations/reorder/', v.locations_reorder, name='locations_reorder'),
    path('events/<int:event_id>/locations/<int:location_id>/', v.location_edit, name='location_edit'),
    path('events/<int:event_id>/locations/<int:location_id>/action/', v.location_action, name='location_action'),

    path('events/<int:event_id>/schedule/', v.schedule, name='schedule'),
    path('events/<int:event_id>/schedule/new/', v.item_edit, name='item_new'),
    path('events/<int:event_id>/schedule/<int:item_id>/', v.item_edit, name='item_edit'),
    path('events/<int:event_id>/schedule/<int:item_id>/action/', v.item_action, name='item_action'),

    path('events/<int:event_id>/notifications/', v.notifications, name='notifications'),
    path('events/<int:event_id>/notifications/<int:notification_id>/', v.notification_action,
         name='notification_action'),

    path('events/<int:event_id>/display/', v.display_settings, name='display'),
    path('events/<int:event_id>/screens/', v.event_screens, name='event_screens'),  # API-024
    path('events/<int:event_id>/limit/', v.event_limit_request, name='event_limit_request'),  # API-025
    path('events/<int:event_id>/paid/', v.event_paid, name='event_paid'),  # staff
    path('events/<int:event_id>/theme/', v.event_theme, name='event_theme'),
    path('events/<int:event_id>/theme/css/', v.event_theme_css, name='event_theme_css'),
    path('events/<int:event_id>/theme/switches/', v.event_theme_switches, name='event_theme_switches'),
    path('events/<int:event_id>/theme/generate/', v.event_theme_generate, name='event_theme_generate'),
    path('events/<int:event_id>/changes/', v.changesets, name='changesets'),
    path('events/<int:event_id>/history/', v.history, name='history'),
    path('events/<int:event_id>/team/', v.team, name='team'),
    path('events/<int:event_id>/usage/', v.usage, name='usage'),
    path('usage/', v.admin_usage, name='admin_usage'),
]
