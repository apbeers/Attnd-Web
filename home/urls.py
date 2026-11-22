from django.urls import path

from home import views
from platform_admin.views_arch import brand_public

app_name = 'home'
urlpatterns = [
    # Home Page
    path('', views.home, name='homepage'),
    path('home/', views.home, name='home_page'),  # the attnd. logo: always the home page, signed in or not
    path('privacy/', views.privacy, name='privacy'),
    path('brand/', brand_public, name='brand'),  # the public brand center (batch 7)
    # Onboarding (Nov 21, 2026): the composer, the page's anonymous counts, "Happening near you", Claim it
    path('start/', views.start, name='start'),
    path('home/m/', views.metric, name='metric'),
    path('home/near/', views.near, name='near'),
    path('claim/<str:event_code>/', views.claim, name='claim'),
]
