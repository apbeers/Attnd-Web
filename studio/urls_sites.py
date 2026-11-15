"""URLconf of SITES_HOST (selected by studio.middleware.SitesHostMiddleware). Nothing else exists there."""
from django.urls import re_path

from studio import views_sites

urlpatterns = [
    re_path(r'^(?P<slug>[a-z0-9][a-z0-9-]{1,48}[a-z0-9])(?P<rest>/.*)?$', views_sites.serve, name='site'),
]

handler404 = views_sites.not_found
