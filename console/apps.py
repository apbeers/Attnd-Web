from django.apps import AppConfig


class ConsoleConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'console'

    def ready(self):
        from django.db.models.signals import post_migrate
        from attnd.permissions import sync_roles
        post_migrate.connect(sync_roles, dispatch_uid='attnd.sync_roles')
        from console import recent_auth, session_expiry, signals, twofactor, user_sessions  # noqa: F401  (signal receivers)
