from django.urls import path

from home import views

app_name = 'home'
urlpatterns = [
    # Home Page
    path('', views.home, name='homepage'),
    path('home/', views.home, name='home_page'),  # the attnd. logo: always the home page, signed in or not
    path('privacy/', views.privacy, name='privacy'),
]
