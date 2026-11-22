from django.urls import path

from credit import views

app_name = 'credit'
urlpatterns = [
    path('', views.spend_page, name='spend'),
    path('limits/', views.save_limits, name='limits'),
    path('overrides/', views.save_override, name='override'),
    path('overrides/remove/', views.remove_override, name='override_remove'),
    path('export.csv', views.export_csv, name='export'),
]
