from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import path, include

urlpatterns = [
                  path('', include('home.urls'), name='home'),
                  path('admin/', admin.site.urls),
                  path('console/', include('console.urls'), name='console'),
                  path('events/', include('events.urls'), name='events'),
                  path('accounts/', include('django.contrib.auth.urls')),
              ] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT) + static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
