from django.urls import path

from scrape import views

app_name = 'scrape'
urlpatterns = [
    path('', views.scrape_view, name='scrape'),
]
