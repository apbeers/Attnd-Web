from django.urls import path

from home import views

app_name = 'home'
urlpatterns = [
    # Home Page
    path('', views.home, name='homepage'),
    path('privacy/', views.privacy, name='privacy'),
]
