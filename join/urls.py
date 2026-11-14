from django.urls import path
from join import views

app_name = 'join'
urlpatterns = [
    path('<str:event_code>/', views.JoinEventRedirect.as_view(), name='join_event'),
    path('<str:event_code>/qr.svg', views.join_qr, name='join_qr'),
]
