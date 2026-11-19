from django.urls import path

from studio import views as v

app_name = 'studio'
urlpatterns = [
    path('studio/reports/', v.reports, name='reports'),
    path('events/<int:event_id>/studio/', v.editor, name='editor'),
    path('events/<int:event_id>/studio/state/', v.state, name='state'),
    path('events/<int:event_id>/studio/app-preview/', v.app_preview, name='app_preview'),
    path('events/<int:event_id>/studio/chat/', v.chat, name='chat'),
    path('events/<int:event_id>/studio/runs/<int:run_id>/', v.run_status, name='run'),
    path('events/<int:event_id>/studio/runs/<int:run_id>/retry/', v.run_retry, name='run_retry'),
    path('events/<int:event_id>/studio/code/', v.code, name='code'),
    path('events/<int:event_id>/studio/undo/', v.undo, name='undo'),
    path('events/<int:event_id>/studio/publish/', v.publish, name='publish'),
    path('events/<int:event_id>/studio/unpublish/', v.unpublish, name='unpublish'),
    path('events/<int:event_id>/studio/versions/<int:version_id>/restore/', v.restore, name='restore'),
    path('events/<int:event_id>/studio/versions/<int:version_id>/publish/', v.publish, name='publish_version'),
    path('events/<int:event_id>/studio/versions/<int:version_id>/preview/', v.version_preview, name='version_preview'),
    path('events/<int:event_id>/studio/preview-link/', v.preview_link, name='preview_link'),
    path('events/<int:event_id>/studio/settings/', v.site_settings, name='settings'),
    path('events/<int:event_id>/studio/import/', v.bring_in, name='import'),
    path('events/<int:event_id>/studio/assets/', v.assets_upload, name='assets'),
    path('events/<int:event_id>/studio/assets/<uuid:asset_id>/', v.asset_action, name='asset'),
    path('events/<int:event_id>/studio/flyer/', v.flyer, name='flyer'),
    path('events/<int:event_id>/studio/forms/<slug:form_name>.csv', v.form_csv, name='form_csv'),
    path('events/<int:event_id>/studio/forms/entries/<int:submission_id>/delete/', v.form_delete, name='form_delete'),
]
