import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

DEBUG = bool(os.getenv('DEBUG'))

SECRET_KEY = os.getenv('SECRET_KEY', '')
if not SECRET_KEY:
    if DEBUG or os.getenv('CI'):
        SECRET_KEY = 'insecure-dev-only-secret-key'
    else:
        raise RuntimeError('SECRET_KEY must be set in production')

# HTTPS only outside local development (DEBUG). Heroku terminates TLS and sets X-Forwarded-Proto.
SECURE_SSL_REDIRECT = not DEBUG
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
LANGUAGE_COOKIE_SECURE = not DEBUG
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
# Browsers remember to use HTTPS only (1 year, every subdomain). Not on the preload list (hard to undo).
SECURE_HSTS_SECONDS = 0 if DEBUG else int(os.getenv('SECURE_HSTS_SECONDS', str(365 * 24 * 3600)))
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = False
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = 'Lax'

if DEBUG:
    ALLOWED_HOSTS = ['*']
else:
    ALLOWED_HOSTS = ['.herokuapp.com', '.attnd.app']

# Local runs (manage.py runserver/test, health checks from inside the dyno). This used to also switch off
# the HTTPS redirect and secure cookies everywhere, because this list always contains localhost; local
# development runs with DEBUG=1, which already does that (see the top of this file).
ALLOWED_HOSTS += ['127.0.0.1', 'localhost']

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django.contrib.sitemaps',
    'cloudinary_storage',
    'cloudinary',
    'events.apps.EventsConfig',
    'console.apps.ConsoleConfig',
    'home.apps.HomeConfig',
    'join.apps.JoinConfig',
    'scrape.apps.ScrapeConfig',
    'attestation.apps.AttestationConfig',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'attnd.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'console.context_processors.maps',
            ],
        },
    },
]

WSGI_APPLICATION = 'attnd.wsgi.application'

# PostgreSQL everywhere (Heroku, CI, local, tests), so behaviour never differs between them.
# Without DATABASE_URL this points at the local database from `scripts/dev_db.sh` (Postgres 18 in Docker).
import dj_database_url  # noqa: E402

DATABASES = {
    'default': dj_database_url.config(
        default='postgres://attnd:attnd@localhost:5432/attnd', conn_max_age=600, conn_health_checks=True),
}

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True

STATIC_URL = '/static/'
STATIC_ROOT = os.path.join(BASE_DIR, 'staticfiles')
WHITENOISE_ROOT = os.path.join(BASE_DIR, 'public')
MEDIA_ROOT = os.path.join(BASE_DIR, 'media')
MEDIA_URL = '/media/'

STORAGES = {
    'default': {
        'BACKEND': 'django.core.files.storage.FileSystemStorage',
    },
    'staticfiles': {
        'BACKEND': 'whitenoise.storage.CompressedManifestStaticFilesStorage',
    },
}

# Tests render templates without running collectstatic first
import sys  # noqa: E402
TESTING = len(sys.argv) > 1 and sys.argv[1] == 'test'
if TESTING:
    STORAGES['staticfiles']['BACKEND'] = 'django.contrib.staticfiles.storage.StaticFilesStorage'

# Silent "content_updated" push whenever attendee-visible content changes (events.push.content_changed).
CONTENT_PUSH_ENABLED = not TESTING and os.getenv('CONTENT_PUSH_ENABLED', '1') not in ('0', 'false', 'False')
CONTENT_PUSH_DELAY = int(os.getenv('CONTENT_PUSH_DELAY', '5'))
CONTENT_PUSH_MIN_INTERVAL = int(os.getenv('CONTENT_PUSH_MIN_INTERVAL', '60'))

LOGIN_URL = '/accounts/login/'
LOGIN_REDIRECT_URL = '/console/'
LOGOUT_REDIRECT_URL = '/'

API_KEY = os.getenv('API_KEY', '')
CONTACT_EMAIL = os.getenv('CONTACT_EMAIL', '')

# Website watching (scrape.watch): limits on AI runs triggered by detected page changes, per event.
WATCH_MIN_AI_GAP_MINUTES = int(os.getenv('WATCH_MIN_AI_GAP_MINUTES', '15'))
WATCH_MAX_AI_RUNS_PER_DAY = int(os.getenv('WATCH_MAX_AI_RUNS_PER_DAY', '24'))

# Organizers sign in with their email (or username), case-insensitively.
AUTHENTICATION_BACKENDS = ['console.auth_backends.EmailOrUsernameBackend']

# Google Maps in the console (address autocomplete, pin map). A *browser* key restricted to our domains
# (HTTP referrers) for the Maps JavaScript API and Places API (New); not the server key above.
GOOGLE_MAPS_BROWSER_KEY = os.getenv('GOOGLE_MAPS_BROWSER_KEY', '')
GOOGLE_MAPS_MAP_ID = os.getenv('GOOGLE_MAPS_MAP_ID', 'DEMO_MAP_ID')

# The always-current demo event (manage.py load_demo_event); run_scheduled_tasks reloads it before it
# ends so App Store / Play reviewers and demos always see an event that is happening now. Empty = off.
DEMO_EVENT_CODE = os.getenv('DEMO_EVENT_CODE', 'DEMO')

# Store links, used by /join/<code>/ and the apps' update prompt (/api/app-status/).
IOS_APP_STORE_URL = os.getenv('IOS_APP_STORE_URL', '')  # https://apps.apple.com/app/id<numeric Apple ID>
ANDROID_PACKAGE_NAME = os.getenv('ANDROID_PACKAGE_NAME', 'com.arcu.attnd')

# Update prompt (API_CHANGES.md, API-003). Builds older than APP_MIN_VERSION_<PLATFORM> must update;
# builds older than APP_LATEST_VERSION_<PLATFORM> are told an update is available. Empty = no prompt.
APP_MIN_VERSION_IOS = os.getenv('APP_MIN_VERSION_IOS', '')
APP_MIN_VERSION_ANDROID = os.getenv('APP_MIN_VERSION_ANDROID', '')
APP_LATEST_VERSION_IOS = os.getenv('APP_LATEST_VERSION_IOS', '')
APP_LATEST_VERSION_ANDROID = os.getenv('APP_LATEST_VERSION_ANDROID', '')
APP_UPDATE_MESSAGE = os.getenv('APP_UPDATE_MESSAGE', '')
# API versions the server still serves. An app on any other version is told to update.
SUPPORTED_API_VERSIONS = [int(v) for v in os.getenv('SUPPORTED_API_VERSIONS', '1,2').split(',') if v.strip()]
# Production should set STATIC_API_KEY_ENABLED=0 so only attested official apps can call the API.
STATIC_API_KEY_ENABLED = os.getenv('STATIC_API_KEY_ENABLED', '1') not in ('0', 'false', 'False')

# Public base URL of this deployment, for links in emails (dev sets its herokuapp URL).
SITE_URL = os.getenv('SITE_URL', 'https://attnd.app').rstrip('/')

# Email: Mailgun SMTP (Heroku add-on; SETUP.md step 11). Production sends from mg.attnd.app; dev uses its
# own Mailgun sandbox, which only delivers to authorized recipients. EMAIL_HOST_USER/PASSWORD are the
# domain's SMTP credentials (the add-on doesn't provide a password). Without a password, mail is printed
# to the log instead of sent.
EMAIL_HOST = os.getenv('MAILGUN_SMTP_SERVER', 'smtp.mailgun.org')
EMAIL_PORT = int(os.getenv('MAILGUN_SMTP_PORT', '587'))
EMAIL_USE_TLS = True
EMAIL_TIMEOUT = 15
EMAIL_HOST_USER = os.getenv('EMAIL_HOST_USER') or os.getenv('MAILGUN_SMTP_LOGIN', '')
EMAIL_HOST_PASSWORD = os.getenv('EMAIL_HOST_PASSWORD') or os.getenv('MAILGUN_SMTP_PASSWORD', '')
DEFAULT_FROM_EMAIL = os.getenv('DEFAULT_FROM_EMAIL', 'Attnd <noreply@mg.attnd.app>')
SERVER_EMAIL = DEFAULT_FROM_EMAIL
# Comma-separated; these get server error emails.
ADMINS = [('Attnd admin', e.strip()) for e in os.getenv('ADMIN_EMAILS', '').split(',') if e.strip()]
# Comma-separated addresses or @domains; when set, mail to anyone else is logged and dropped (use on dev).
EMAIL_ALLOWED_RECIPIENTS = [e.strip().lower() for e in os.getenv('EMAIL_ALLOWED_RECIPIENTS', '').split(',') if e.strip()]
EMAIL_BACKEND = ('console.mail.AllowListSMTPBackend' if EMAIL_HOST_PASSWORD
                 else 'django.core.mail.backends.console.EmailBackend')

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

if os.getenv('CLOUDINARY_URL'):
    STORAGES['default']['BACKEND'] = 'cloudinary_storage.storage.MediaCloudinaryStorage'


LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "[{levelname}] {name}: {message}",
            "style": "{",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "verbose",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": "INFO",
    },
    "loggers": {
        "scrape": {
            "handlers": ["console"],
            "level": "DEBUG",
            "propagate": False,
        },
    },
}

GOOGLE_PLACES_API_KEY = os.getenv('GOOGLE_PLACES_API_KEY', '')

# ---------------------------------------------------------------------------
# Apple App Attest
# ---------------------------------------------------------------------------
# The bundle ID and team ID are embedded in the attestation object's rpIdHash.
# Set APPLE_ATTEST_ENVIRONMENT = 'development' in your local .env to allow
# sandbox attestations from Xcode simulators / TestFlight builds.
APPLE_APP_BUNDLE_ID = os.getenv('APPLE_APP_BUNDLE_ID', '')
APPLE_TEAM_ID = os.getenv('APPLE_TEAM_ID', '')
APPLE_ATTEST_ENVIRONMENT = os.getenv('APPLE_ATTEST_ENVIRONMENT', 'production')

# ---------------------------------------------------------------------------
# Google Play Integrity
# ---------------------------------------------------------------------------
# Set to the full contents of your Google service account JSON key file.
# If running on GCP/Cloud Run, leave empty and application default credentials
# will be used automatically.
ANDROID_PACKAGE_NAME = os.getenv('ANDROID_PACKAGE_NAME', '')
GOOGLE_SERVICE_ACCOUNT_JSON = os.getenv('GOOGLE_SERVICE_ACCOUNT_JSON', '')

# ---------------------------------------------------------------------------
# Attestation JWT
# ---------------------------------------------------------------------------
# Dedicated secret for signing attestation tokens.
# Falls back to SECRET_KEY if not set.
ATTESTATION_JWT_SECRET = os.getenv('ATTESTATION_JWT_SECRET', '')
ATTESTATION_JWT_TTL_HOURS = int(os.getenv('ATTESTATION_JWT_TTL_HOURS', '24'))

# ---------------------------------------------------------------------------
# Push notifications (see events/push.py)
# ---------------------------------------------------------------------------
APNS_AUTH_KEY = os.getenv('APNS_AUTH_KEY', '')
APNS_KEY_ID = os.getenv('APNS_KEY_ID', '')
APNS_TEAM_ID = os.getenv('APNS_TEAM_ID', '')
APNS_TOPIC = os.getenv('APNS_TOPIC', '')
APNS_USE_SANDBOX = bool(os.getenv('APNS_USE_SANDBOX'))
FCM_PROJECT_ID = os.getenv('FCM_PROJECT_ID', '')
FCM_SERVICE_ACCOUNT_JSON = os.getenv('FCM_SERVICE_ACCOUNT_JSON', '')

# ---------------------------------------------------------------------------
# AI event import (see scrape/)
# ---------------------------------------------------------------------------
OPENAI_MODEL = os.getenv('OPENAI_MODEL', 'gpt-5-mini')
# Run AI jobs in a background thread of the web dyno when no worker dyno is running.
INGEST_RUN_INLINE = os.getenv('INGEST_RUN_INLINE', '1') not in ('0', 'false', 'False', '')

# ---------------------------------------------------------------------------
# Device attestation policy (see attestation/android_attest.py)
# ---------------------------------------------------------------------------
def _env_bool(name, default):
    value = os.getenv(name)
    return default if value is None else value not in ('0', 'false', 'False', '')


ANDROID_ALLOW_UNRECOGNIZED_APP = _env_bool('ANDROID_ALLOW_UNRECOGNIZED_APP', False)
ANDROID_REQUIRE_PLAY_LICENSE = _env_bool('ANDROID_REQUIRE_PLAY_LICENSE', True)
ANDROID_REQUIRE_STRONG_INTEGRITY = _env_bool('ANDROID_REQUIRE_STRONG_INTEGRITY', False)
ANDROID_SIGNING_CERT_SHA256 = os.getenv('ANDROID_SIGNING_CERT_SHA256', '') or os.getenv('ANDROID_SHA256_FINGERPRINT', '')

