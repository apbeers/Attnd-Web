from django.urls import path

from diagnostics import views

app_name = 'diagnostics'
urlpatterns = [
    path('', views.overview, name='overview'),
    path('s/<str:code>/', views.session_detail, name='session'),
    path('s/<str:code>/entries.json', views.session_entries, name='session_entries'),
    path('s/<str:code>/export.jsonl', views.session_export, name='session_export'),
    path('s/<str:code>/delete/', views.session_delete, name='session_delete'),
    path('reports/', views.reports, name='reports'),
    path('reports/<int:pk>/', views.report_detail, name='report'),
    path('endpoints/', views.endpoints, name='endpoints'),
    path('symbols/', views.symbols, name='symbols'),
]
