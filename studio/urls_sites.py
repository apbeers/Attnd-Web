"""URLconf of SITES_HOST (selected by studio.middleware.SitesHostMiddleware). Nothing else exists there."""
from django.urls import re_path

from studio import views_sites

from attnd.public import mark

urlpatterns = mark([  # the hosted sites are public (no sign-in, no cookies there)
    re_path(r'^(?P<slug>[a-z0-9][a-z0-9-]{1,48}[a-z0-9])(?P<rest>/.*)?$', views_sites.serve, name='site'),
])

handler404 = views_sites.not_found
