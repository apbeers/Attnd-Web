from django.urls import path
from console import views

urlpatterns = [
    path('', views.home, name='home')
]
