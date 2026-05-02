# ── Changes to apply to attnd/urls.py ────────────────────────────────────────
#
# 1. Add the health view import at the top of the file:
#
#    BEFORE:
#        from home.sitemaps import StaticSitemap
#        from join import views as join_views
#
#    AFTER:
from attnd import views as attnd_views
from home.sitemaps import StaticSitemap
from join import views as join_views

# 2. Add the health endpoint to urlpatterns:
#
#    BEFORE:
#        urlpatterns = [
#            path('sitemap.xml', ...),
#            ...
#        ]
#
#    AFTER: Add this path anywhere in urlpatterns (recommend first for fast ALB checks):
#        path('health/', attnd_views.health, name='health'),
#
#    Final urlpatterns should look like:
urlpatterns = [
    # ALB health check — must be unauthenticated and fast
    # path('health/', attnd_views.health, name='health'),
    # ... rest of existing urlpatterns unchanged
]
