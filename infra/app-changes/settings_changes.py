# ── Changes to apply to attnd/settings.py ────────────────────────────────────
#
# 1. Replace the ALLOWED_HOSTS block:
#
#    BEFORE:
#        if DEBUG:
#            ALLOWED_HOSTS = ['*']
#        else:
#            ALLOWED_HOSTS = ['.herokuapp.com', '.attnd.app']
#
#    AFTER:
if DEBUG:
    ALLOWED_HOSTS = ['*']
elif os.getenv('ALLOWED_HOSTS'):
    # ECS injects this as a comma-separated env var (see ecs.tf)
    ALLOWED_HOSTS = os.getenv('ALLOWED_HOSTS').split(',')
else:
    ALLOWED_HOSTS = ['.attnd.app']


# 2. Replace the LOGGING block:
#
#    Add "python-json-logger>=2.0.7" to pyproject.toml first, then run `uv lock`.
#
#    BEFORE: the existing LOGGING dict
#    AFTER:
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "[{levelname}] {name}: {message}",
            "style": "{",
        },
        # JSON formatter — CloudWatch Insights parses fields like levelname, name, message
        "json": {
            "()": "pythonjsonlogger.json.JsonFormatter",
            "fmt": "%(asctime)s %(levelname)s %(name)s %(message)s",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            # ECS ships stdout to CloudWatch via awslogs driver — use JSON in prod
            "formatter": "json" if not DEBUG else "verbose",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": "DEBUG" if DEBUG else "INFO",
    },
    "loggers": {
        "scrape": {
            "handlers": ["console"],
            "level": "DEBUG",
            "propagate": False,
        },
        "django.security.DisallowedHost": {
            "handlers": ["console"],
            "level": "ERROR",
            "propagate": False,
        },
    },
}
