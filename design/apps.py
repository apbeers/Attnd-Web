from django.apps import AppConfig


class DesignConfig(AppConfig):
    """Attnd's one design system (docs/design_system.md): the shared CSS layers and the bundles pages load."""
    name = 'design'
    default_auto_field = 'django.db.models.BigAutoField'
