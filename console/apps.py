from django.apps import AppConfig


class ConsoleConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'console'

    def ready(self):
        from console import recent_auth, session_expiry, signals, twofactor, user_sessions  # noqa: F401  (signal receivers)
