from django.urls import path

from console import views

app_name = 'console'
urlpatterns = [
    path('signup/', views.SignUpView.as_view(), name='signup'),
]
