from django.urls import path

from mcp_server import views

app_name = 'mcp'
urlpatterns = [
    path('confirm/<int:step_id>/', views.confirm, name='confirm'),
]
