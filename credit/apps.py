from django.apps import AppConfig


class CreditConfig(AppConfig):
    name = 'credit'
    verbose_name = 'AI credit'
    default_auto_field = 'django.db.models.BigAutoField'

    def ready(self):
        from credit import signals  # noqa: F401  (carry a visitor's spend into the account on sign-in)
