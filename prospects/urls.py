from django.urls import path

from prospects import views

app_name = 'prospects'
urlpatterns = [
    path('', views.prospect_list, name='list'),
    path('export.csv', views.export_csv, name='export'),
    path('<int:pk>/', views.prospect_detail, name='detail'),
    path('<int:pk>/pitch/', views.pitch, name='pitch'),
    path('pitches/<int:pitch_id>/status/', views.pitch_status, name='pitch_status'),
    path('do-not-contact/', views.dnc_email, name='dnc_email'),
    path('runs/', views.runs, name='runs'),
    path('runs/<int:pk>/', views.run_detail, name='run'),
    path('settings/', views.settings_view, name='settings'),
    path('claims/', views.claims_view, name='claims'),
]
