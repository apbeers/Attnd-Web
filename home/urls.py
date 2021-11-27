from django.urls import path

from console import views

app_name = 'home'
urlpatterns = [
    # Home Page
    path('', views.home, name='homepage'),
]
