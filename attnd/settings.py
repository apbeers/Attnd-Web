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
# security.W021 ("not preloaded") is deliberate: the preload list is hard to leave again.
SILENCED_SYSTEM_CHECKS = ['security.W021']
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
    'django.contrib.postgres',  # GIN trigram + BRIN indexes for diagnostics (built into Django)
    'cloudinary_storage',
    'cloudinary',
    'events.apps.EventsConfig',
    'console.apps.ConsoleConfig',
    'home.apps.HomeConfig',
    'join.apps.JoinConfig',
    'scrape.apps.ScrapeConfig',
    'attestation.apps.AttestationConfig',
    'diagnostics.apps.DiagnosticsConfig',
    'studio.apps.StudioConfig',
    'prospects.apps.ProspectsConfig',
    'agent.apps.AgentConfig',
]

MIDDLEWARE = [
    # Attnd Studio's hosted sites (SITES_HOST): only site routes there, no cookies, the sites' own CSP.
    'studio.middleware.SitesHostMiddleware',
    # First, so it times the whole request: one JSON line per /api/ request + X-Request-Id (API-013).
    'diagnostics.middleware.RequestLogMiddleware',
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    # gzip for the API's JSON and the HTML pages (after WhiteNoise, which serves static files pre-compressed).
    'attnd.middleware.CompressionMiddleware',
    # Django's session middleware + Ask Attnd's internal requests (they use the person's session, never saved).
    'agent.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'console.middleware.TwoFactorMiddleware',
    # Ask Attnd's chat: a component form with errors comes back as just that component (console/components).
    'console.middleware.ComponentFragmentMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'django.middleware.csp.ContentSecurityPolicyMiddleware',
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
                'django.template.context_processors.csp',
                'console.context_processors.maps',
                'console.context_processors.store_links',
                'console.context_processors.signups',
                'studio.context_processors.studio',
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
WHITENOISE_MIMETYPES = {'.webmanifest': 'application/manifest+json'}
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
    # No collectstatic in tests: WhiteNoise finds files through the finders on demand instead of scanning
    # STATIC_ROOT at startup (which warned "No directory at: …/staticfiles/").
    WHITENOISE_AUTOREFRESH = True
    WHITENOISE_USE_FINDERS = True

# Silent "content_updated" push whenever attendee-visible content changes (events.push.content_changed).
CONTENT_PUSH_ENABLED = not TESTING and os.getenv('CONTENT_PUSH_ENABLED', '1') not in ('0', 'false', 'False')
CONTENT_PUSH_DELAY = int(os.getenv('CONTENT_PUSH_DELAY', '3'))
CONTENT_PUSH_MIN_INTERVAL = int(os.getenv('CONTENT_PUSH_MIN_INTERVAL', '15'))

# Rate limits (attnd.ratelimit) and other per-process counters (scrape/'s "try it" limit) live here.
# LocMemCache is shared by the gthread threads of one process, and Heroku runs a single web process
# (Procfile: 1 worker), so the counts are global. With more web processes or dynos each process would
# count on its own (limits effectively multiply); switch to DatabaseCache (+ `createcachetable` in the
# Procfile release line) then.
CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
        'LOCATION': 'attnd',
        'OPTIONS': {'MAX_ENTRIES': 10000},
    },
}


def _rate(name, default):
    """A limit as (count, window seconds) from env RATE_LIMIT_<NAME>="count/seconds", e.g. "5/900"."""
    value = os.getenv(f'RATE_LIMIT_{name.upper()}', default)
    count, _, window = value.partition('/')
    return int(count), int(window)


# Sign-in, password reset, signup and two-factor attempts (attnd.ratelimit). Override per deployment with
# RATE_LIMIT_<NAME> (e.g. RATE_LIMIT_LOGIN_EMAIL=10/900) or in tests with override_settings(RATE_LIMITS=…).
RATE_LIMITS = {
    'login_email': _rate('login_email', '5/900'),     # failed sign-ins per email, 15 min
    'login_ip': _rate('login_ip', '20/900'),          # failed sign-ins per IP, 15 min
    'reset_email': _rate('reset_email', '3/3600'),    # password reset requests per email, 1 hour
    'reset_ip': _rate('reset_ip', '10/3600'),         # password reset requests per IP, 1 hour
    'signup_ip': _rate('signup_ip', '10/3600'),       # signup attempts per IP, 1 hour
    'otp_user': _rate('otp_user', '5/900'),           # wrong two-factor codes per account, 15 min
    'passkey_ip': _rate('passkey_ip', '20/900'),      # failed passkey sign-ins per IP, 15 min
    'email_code_user': _rate('email_code_user', '3/900'),  # sign-in codes emailed per account, 15 min
    'csp_report_ip': _rate('csp_report_ip', '60/60'),  # CSP violation reports logged per IP, 1 min
    'diagnostics_ip': _rate('diagnostics_ip', '600/600'),  # debug log uploads per IP, 10 min (API-013)
    'diagnostics_reports_ip': _rate('diagnostics_reports_ip', '60/3600'),  # crash/hang report uploads per IP
    'studio_form_ip': _rate('studio_form_ip', '10/600'),       # Studio site form posts per IP per site, 10 min
    'studio_form_site': _rate('studio_form_site', '300/3600'),  # Studio site form posts per site, 1 hour
    'studio_report_ip': _rate('studio_report_ip', '5/3600'),    # "Report this site" per IP, 1 hour
    'scrape_ip': _rate('scrape_ip', '5/3600'),                  # homepage trial AI imports per IP, 1 hour
    'attest_nonce_ip': _rate('attest_nonce_ip', '30/60'),       # attestation challenges per IP, 1 minute
}
# Heroku's router appends the connecting IP to X-Forwarded-For; the last entry is the one to trust.
# Set to 0 when not behind such a proxy (the header could then be forged freely).
RATE_LIMIT_TRUST_X_FORWARDED_FOR = os.getenv('RATE_LIMIT_TRUST_X_FORWARDED_FOR', '1') not in ('0', 'false', 'False')

# The Django admin lives at a non-obvious path (/admin/ is a 404). Example: ADMIN_URL_PATH=backstage-7f3/
ADMIN_URL_PATH = os.getenv('ADMIN_URL_PATH', 'manage-attnd/').strip('/') + '/'
if ADMIN_URL_PATH in ('/', 'admin/'):
    raise RuntimeError('ADMIN_URL_PATH must be a non-empty path other than admin/')

# Two-factor sign-in (console.twofactor): staff and superusers must use it; anyone can turn it on.
TWO_FACTOR_ISSUER = os.getenv('TWO_FACTOR_ISSUER', 'Attnd')

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
IOS_APP_STORE_URL = os.getenv('IOS_APP_STORE_URL', 'https://apps.apple.com/us/app/attnd/id1470627406')
ANDROID_PACKAGE_NAME = os.getenv('ANDROID_PACKAGE_NAME', 'com.arcu.attnd')
ANDROID_PLAY_STORE_URL = f'https://play.google.com/store/apps/details?id={ANDROID_PACKAGE_NAME}'

# Update prompt (API_CHANGES.md, API-003). Builds older than APP_MIN_VERSION_<PLATFORM> must update;
# builds older than APP_LATEST_VERSION_<PLATFORM> are told an update is available. Empty = no prompt.
APP_MIN_VERSION_IOS = os.getenv('APP_MIN_VERSION_IOS', '')
APP_MIN_VERSION_ANDROID = os.getenv('APP_MIN_VERSION_ANDROID', '')
APP_LATEST_VERSION_IOS = os.getenv('APP_LATEST_VERSION_IOS', '')
APP_LATEST_VERSION_ANDROID = os.getenv('APP_LATEST_VERSION_ANDROID', '')
APP_UPDATE_MESSAGE = os.getenv('APP_UPDATE_MESSAGE', '')
# API versions the server still serves. An app on any other version is told to update.
SUPPORTED_API_VERSIONS = [int(v) for v in os.getenv('SUPPORTED_API_VERSIONS', '2').split(',') if v.strip()]
# Production should set STATIC_API_KEY_ENABLED=0 so only attested official apps can call the API.
STATIC_API_KEY_ENABLED = os.getenv('STATIC_API_KEY_ENABLED', '1') not in ('0', 'false', 'False')
# CI keys (API-008): comma-separated bearer tokens for the apps' live integration tests against production.
# Separate from API_KEY and still accepted with STATIC_API_KEY_ENABLED=0, but only for the DEMO event
# (lookup, nearby, its bundle/join/leave/usage) and for `ci-…` device ids; usage they post is discarded.
CI_API_KEYS = [k.strip() for k in os.getenv('CI_API_KEYS', '').split(',') if k.strip()]

# Diagnostics (API-013, diagnostics/): debug log sessions, crash reports, R8 mappings, endpoint timings.
# The tables together stay under DIAGNOSTICS_MAX_MB (Heroku essential-0 Postgres has 1 GB in all); log entries
# are kept 7 days from dev builds and 14 days from production debug mode (purged by run_scheduled_tasks).
DIAGNOSTICS_MAX_BYTES = int(os.getenv('DIAGNOSTICS_MAX_MB', '250')) * 1024 * 1024
DIAGNOSTICS_RETENTION_DAYS = {'dev': int(os.getenv('DIAGNOSTICS_DEV_DAYS', '7')),
                              'debug_mode': int(os.getenv('DIAGNOSTICS_DEBUG_DAYS', '14'))}
DIAGNOSTICS_SESSION_WINDOW = 600  # seconds
DIAGNOSTICS_SESSION_MAX_ENTRIES = int(os.getenv('DIAGNOSTICS_SESSION_MAX_ENTRIES', '20000'))  # per window
DIAGNOSTICS_MAX_SYMBOL_FILES = int(os.getenv('DIAGNOSTICS_MAX_SYMBOL_FILES', '20'))
DIAGNOSTICS_VACUUM_FULL = True
DIAGNOSTICS_TIMING = not TESTING  # endpoint timings (tests turn it on where they check it)
# iOS symbolication: the panel dispatches symbolicate.yml in attnd-ios (fine-grained token with
# "Actions: read and write" on that repo only). Unset = the Symbolicate button explains what's missing.
GITHUB_SYMBOLICATE_TOKEN = os.getenv('GITHUB_SYMBOLICATE_TOKEN', '')
GITHUB_SYMBOLICATE_REPO = os.getenv('GITHUB_SYMBOLICATE_REPO', 'apbeers/Attnd-iOS')
GITHUB_SYMBOLICATE_REF = os.getenv('GITHUB_SYMBOLICATE_REF', 'main')

# Public base URL of this deployment, for links in emails (dev: https://dev.attnd.app).
SITE_URL = os.getenv('SITE_URL', 'https://attnd.app').rstrip('/')

# Passkeys (WebAuthn, console.webauthn). A passkey belongs to one domain (the relying party ID): passkeys
# made on the dev app (dev.attnd.app) don't work on attnd.app and the other way round.
# WEBAUTHN_RP_ID defaults to SITE_URL's host; WEBAUTHN_ORIGINS (comma-separated) to SITE_URL's origin, plus
# http://localhost:8000 with DEBUG (pages served from localhost then use the RP ID "localhost"; WebAuthn
# doesn't work on IP addresses such as 127.0.0.1).
from urllib.parse import urlsplit as _urlsplit  # noqa: E402

_site = _urlsplit(SITE_URL)
WEBAUTHN_RP_ID = os.getenv('WEBAUTHN_RP_ID', '').strip().lower() or (_site.hostname or 'localhost')
WEBAUTHN_RP_NAME = os.getenv('WEBAUTHN_RP_NAME', 'Attnd')
WEBAUTHN_ORIGINS = [o.strip().rstrip('/') for o in os.getenv('WEBAUTHN_ORIGINS', '').split(',') if o.strip()] or [
    f'{_site.scheme}://{_site.netloc}']
# Production also answers at www.attnd.app (a Heroku domain); a passkey for the RP ID attnd.app works there too.
if _site.hostname == 'attnd.app':
    WEBAUTHN_ORIGINS += [o for o in (f'{_site.scheme}://www.{_site.netloc}',) if o not in WEBAUTHN_ORIGINS]
if DEBUG:
    WEBAUTHN_ORIGINS += [o for o in ('http://localhost:8000',) if o not in WEBAUTHN_ORIGINS]

# Email: Mailgun SMTP (Heroku add-on; SETUP.md step 11). Production sends from mg.attnd.app; dev uses its
# own Mailgun sandbox, which only delivers to authorized recipients. The env vars EMAIL_HOST_USER/PASSWORD are the
# domain's SMTP credentials (the add-on doesn't provide a password). Without a password, mail is printed
# to the log instead of sent.
# Django 6.1 MAILERS (the EMAIL_HOST/EMAIL_PORT/... settings are deprecated). EMAIL_ENABLED says whether real
# mail can be sent (console.mail.email_enabled); EMAIL_SMTP_OPTIONS are the SMTP backend's options.
_SMTP_PASSWORD = os.getenv('EMAIL_HOST_PASSWORD') or os.getenv('MAILGUN_SMTP_PASSWORD', '')
EMAIL_ENABLED = bool(_SMTP_PASSWORD)
EMAIL_SMTP_OPTIONS = {
    'host': os.getenv('MAILGUN_SMTP_SERVER', 'smtp.mailgun.org'),
    'port': int(os.getenv('MAILGUN_SMTP_PORT', '587')),
    'username': os.getenv('EMAIL_HOST_USER') or os.getenv('MAILGUN_SMTP_LOGIN', ''),
    'password': _SMTP_PASSWORD,
    'use_tls': True,
    'timeout': 15,
}
DEFAULT_FROM_EMAIL = os.getenv('DEFAULT_FROM_EMAIL', 'Attnd <noreply@mg.attnd.app>')
SERVER_EMAIL = DEFAULT_FROM_EMAIL
# Comma-separated; these get server error emails.
ADMINS = [('Attnd admin', e.strip()) for e in os.getenv('ADMIN_EMAILS', '').split(',') if e.strip()]
# Comma-separated addresses or @domains; when set, mail to anyone else is logged and dropped (use on dev).
EMAIL_ALLOWED_RECIPIENTS = [e.strip().lower() for e in os.getenv('EMAIL_ALLOWED_RECIPIENTS', '').split(',') if e.strip()]
MAILERS = {
    'default': ({'BACKEND': 'console.mail.AllowListSMTPBackend', 'OPTIONS': EMAIL_SMTP_OPTIONS} if EMAIL_ENABLED
                else {'BACKEND': 'django.core.mail.backends.console.EmailBackend'}),
}

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# ---------------------------------------------------------------------------
# Attnd Studio (studio/, docs/specs/studio_spec.md): AI-designed event websites hosted by Attnd
# ---------------------------------------------------------------------------
# Hosted sites live on a SEPARATE registrable domain, path-based: https://<SITES_HOST>/<slug>/. Until a domain is
# bought, each Heroku app's own herokuapp.com hostname is used (herokuapp.com is on the Public Suffix List, so it is a
# different site from attnd.app and can never read its cookies). Empty = no hosting (the editor still works).
_HEROKU_SITES_HOSTS = {'attnd.app': 'attnd-web-7a02b020850d.herokuapp.com',
                       'dev.attnd.app': 'attnd-dev-7ab893f37020.herokuapp.com'}
SITES_HOST = os.getenv('SITES_HOST', '').strip().lower().rstrip('/') or (
    '127.0.0.1:8000' if DEBUG else _HEROKU_SITES_HOSTS.get(_site.hostname or '', ''))
if SITES_HOST and '*' not in ALLOWED_HOSTS:
    ALLOWED_HOSTS.append(SITES_HOST.split(':')[0])  # a bought sites domain isn't under .attnd.app/.herokuapp.com
SITES_ORIGIN = f"{'http' if DEBUG and SITES_HOST.startswith(('127.0.0.1', 'localhost')) else 'https'}://{SITES_HOST}"
# Console origins allowed to frame hosted pages (the editor's preview); nobody else may frame them.
STUDIO_CONSOLE_ORIGINS = list(dict.fromkeys(WEBAUTHN_ORIGINS))
# Organizer sign-ups (console.signups): off = invite-only (only invited emails can create an account, and the
# homepage's anonymous AI import is closed). Attendees and existing accounts are unaffected.
SIGNUPS_OPEN = os.getenv('SIGNUPS_OPEN', '').strip().lower() in ('1', 'true', 'yes', 'on')
# Studio is for admins (staff and superusers) only. These emails (comma-separated, default none) are an explicit
# override that also get Studio.
STUDIO_BETA_EMAILS = [e.strip().lower() for e in os.getenv('STUDIO_BETA_EMAILS', '').split(',') if e.strip()]
# AI: auto = Anthropic when ANTHROPIC_API_KEY is set, else OpenAI (the existing OPENAI_API_KEY).
ANTHROPIC_API_KEY = os.getenv('ANTHROPIC_API_KEY', '')
STUDIO_AI_PROVIDER = os.getenv('STUDIO_AI_PROVIDER', 'auto').strip().lower()
STUDIO_OPENAI_MODEL = os.getenv('STUDIO_OPENAI_MODEL', 'gpt-5.5')
STUDIO_OPENAI_FALLBACK_MODEL = os.getenv('STUDIO_OPENAI_FALLBACK_MODEL', 'gpt-5')
STUDIO_ANTHROPIC_MODEL = os.getenv('STUDIO_ANTHROPIC_MODEL', 'claude-opus-5-5')
STUDIO_IMAGE_MODEL = os.getenv('STUDIO_IMAGE_MODEL', 'gpt-image-2')
STUDIO_IMAGE_FALLBACK_MODEL = os.getenv('STUDIO_IMAGE_FALLBACK_MODEL', 'gpt-image-1')
STUDIO_VISION_MODEL = os.getenv('STUDIO_VISION_MODEL', '') or os.getenv('OPENAI_MODEL', 'gpt-5-mini')
# AI spend per event per UTC day (all Studio calls: chat, images, asset descriptions). 0 = no Studio AI.
# $10: a full import (a 10-25 page site rebuilt in one run) costs roughly $2-4 with gpt-5.5 and prompt caching, up
# to ~$6 on Claude, so $5 left little or nothing for the chat turns that follow it on the same day.
STUDIO_DAILY_SPEND_CAP_USD = float(os.getenv('STUDIO_DAILY_SPEND_CAP_USD', '10'))
STUDIO_MAX_STEPS = int(os.getenv('STUDIO_MAX_STEPS', '24'))
STUDIO_IMPORT_MAX_STEPS = int(os.getenv('STUDIO_IMPORT_MAX_STEPS', '40'))  # model calls of an import run
STUDIO_RUN_MAX_MINUTES = int(os.getenv('STUDIO_RUN_MAX_MINUTES', '25'))  # wall clock per run (stops, keeps the work)
# Bring into Studio: per run, time and bytes for crawling the old site and copying its files.
STUDIO_IMPORT_SECONDS = int(os.getenv('STUDIO_IMPORT_SECONDS', '300'))
STUDIO_IMPORT_MAX_MB = int(os.getenv('STUDIO_IMPORT_MAX_MB', '150'))
STUDIO_PREVIEW_LINK_DAYS = int(os.getenv('STUDIO_PREVIEW_LINK_DAYS', '7'))
# USD per 1M input / output tokens, and per generated image. Conservative estimates (check the providers' price
# pages); a model missing here is priced at STUDIO_PRICES['default'] so the cap errs on the safe side.
STUDIO_PRICES = {
    'default': (10.0, 40.0),
    'gpt-5.5': (5.0, 30.0),
    'gpt-5': (1.25, 10.0),
    'gpt-5-mini': (0.25, 2.0),
    'claude-opus-5-5': (4.0, 20.0),
    'image': 0.25,
}

# Prospects (prospects/, staff only): find events in a region that suit the app (OpenAI web search), read their
# pages for organizer contacts, and make testing-only copies of them. Runs by hand or on its own schedule.
PROSPECT_MODEL = os.getenv('PROSPECT_MODEL', 'gpt-5.5')
PROSPECT_FALLBACK_MODEL = os.getenv('PROSPECT_FALLBACK_MODEL', 'gpt-5')  # only when PROSPECT_MODEL is unavailable
PROSPECT_DAILY_SPEND_CAP_USD = float(os.getenv('PROSPECT_DAILY_SPEND_CAP_USD', '5'))  # all runs per UTC day
PROSPECT_RUN_MAX_MINUTES = int(os.getenv('PROSPECT_RUN_MAX_MINUTES', '20'))  # wall clock per run
PROSPECT_WEB_SEARCH_CALL_USD = 0.01  # OpenAI's web_search tool: $10 per 1,000 calls (+ its content as input tokens)

# Ask Attnd (agent/): the console's AI agent. Every tool runs the real console view as the signed-in person.
# Provider: the same as Studio's (STUDIO_AI_PROVIDER / ANTHROPIC_API_KEY); on OpenAI these models.
AGENT_MODEL = os.getenv('AGENT_MODEL', 'gpt-5.5')
AGENT_FALLBACK_MODEL = os.getenv('AGENT_FALLBACK_MODEL', 'gpt-5')  # only when AGENT_MODEL is unavailable
AGENT_DAILY_SPEND_CAP_USD = float(os.getenv('AGENT_DAILY_SPEND_CAP_USD', '2'))  # per person per UTC day
AGENT_MAX_STEPS = int(os.getenv('AGENT_MAX_STEPS', '40'))  # model calls per run
AGENT_RUN_MAX_MINUTES = int(os.getenv('AGENT_RUN_MAX_MINUTES', '15'))  # wall clock per run (waiting doesn't count)
AGENT_PLANNING_EFFORT = os.getenv('AGENT_PLANNING_EFFORT', 'medium')  # reasoning effort: first call / after a reply
AGENT_STEP_EFFORT = os.getenv('AGENT_STEP_EFFORT', 'low')  # reasoning effort: routine steps
AGENT_MAX_ATTACHMENT_MB = int(os.getenv('AGENT_MAX_ATTACHMENT_MB', '15'))

# ---------------------------------------------------------------------------
# Content Security Policy (Django's built-in CSP middleware)
# ---------------------------------------------------------------------------
# CSP_MODE=enforce (default) | report (Content-Security-Policy-Report-Only: browsers only report) | off.
# Inline <script> blocks carry nonce="{{ csp_nonce }}"; inline event handlers (onclick=…) are not used.
# Violations are posted to /csp-report/ and logged (logger attnd.csp).
from django.utils.csp import CSP  # noqa: E402

CSP_MODE = os.getenv('CSP_MODE', 'enforce').strip().lower()
if CSP_MODE not in ('enforce', 'report', 'off'):
    raise RuntimeError('CSP_MODE must be enforce, report or off')

# Google Maps JavaScript API (console address autocomplete and pin map, console/static/console/maps.js),
# following Google's allowlist CSP guidance for the Maps JavaScript API. Only when a browser key is set.
_MAPS = GOOGLE_MAPS_BROWSER_KEY != ''
_MAPS_SCRIPT = ['https://*.googleapis.com', 'https://*.gstatic.com', '*.google.com', 'https://*.ggpht.com',
                '*.googleusercontent.com', 'blob:'] if _MAPS else []
_MAPS_CONNECT = ['https://*.googleapis.com', '*.google.com', 'https://*.gstatic.com', 'data:', 'blob:'] if _MAPS else []

CSP_POLICY = {
    'default-src': [CSP.SELF],
    'script-src': [CSP.SELF, CSP.NONCE, *_MAPS_SCRIPT],
    # Style attributes and <style> blocks are used across the site, and Google Maps injects styles.
    'style-src': [CSP.SELF, CSP.UNSAFE_INLINE, *(['https://fonts.googleapis.com'] if _MAPS else [])],
    # https: because organizers can point location images and event logos at any website (image_url,
    # logo_url); Cloudinary (res.cloudinary.com), OpenStreetMap tiles (tile.openstreetmap.org, console.js)
    # and Google Maps tiles are covered by it. data: = generated QR codes, blob: = Maps.
    'img-src': [CSP.SELF, 'data:', 'blob:', 'https:', *([SITES_ORIGIN] if SITES_HOST else [])],  # + Studio assets
    'font-src': [CSP.SELF, *(['https://fonts.gstatic.com'] if _MAPS else [])],
    'connect-src': [CSP.SELF, *_MAPS_CONNECT],
    # Studio's draft previews come from the sites host (a separate site), always in a sandboxed iframe.
    'frame-src': (['*.google.com'] if _MAPS else [CSP.SELF]) + ([SITES_ORIGIN] if SITES_HOST else []),
    'worker-src': [CSP.SELF, 'blob:'],  # /console/sw.js; Maps uses blob: workers
    'frame-ancestors': [CSP.NONE],
    'base-uri': [CSP.SELF],
    'form-action': [CSP.SELF],
    'object-src': [CSP.NONE],
    'report-uri': '/csp-report/',
}
SECURE_CSP = CSP_POLICY if CSP_MODE == 'enforce' else {}
SECURE_CSP_REPORT_ONLY = CSP_POLICY if CSP_MODE == 'report' else {}

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
        "message": {
            "format": "{message}",
            "style": "{",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "verbose",
        },
        "request": {
            "class": "logging.StreamHandler",
            "formatter": "message",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": "INFO",
    },
    "loggers": {
        # One JSON line per API request (diagnostics.middleware), the message alone.
        "attnd.request": {
            "handlers": ["request"],
            "level": "INFO",
            "propagate": False,
        },
        "attnd.csp": {
            "handlers": ["console"],
            "level": "WARNING",
            "propagate": False,
        },
        "scrape": {
            "handlers": ["console"],
            "level": "DEBUG",
            "propagate": False,
        },
        # Ask Attnd: one JSON line per internal request (run, step, method, path, status) and run failures.
        "attnd.agent": {
            "handlers": ["request"],
            "level": "INFO",
            "propagate": False,
        },
    },
}
if TESTING:
    # Many tests deliberately get 4xx answers (404, 401, 429...), which django.request logs as warnings.
    # Only its errors (5xx, unhandled exceptions) stay visible in test output.
    LOGGING["loggers"]["django.request"] = {"handlers": ["console"], "level": "ERROR", "propagate": False}
    LOGGING["loggers"]["attnd.request"]["level"] = "WARNING"  # one line per API call would flood the output
    LOGGING["loggers"]["attnd.agent"]["level"] = "WARNING"

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
# ANDROID_PACKAGE_NAME is set above (default com.arcu.attnd); it used to be redefined here with an empty default.
GOOGLE_SERVICE_ACCOUNT_JSON = os.getenv('GOOGLE_SERVICE_ACCOUNT_JSON', '')

# ---------------------------------------------------------------------------
# Attestation JWT
# ---------------------------------------------------------------------------
# Dedicated secret for signing attestation tokens.
# Falls back to SECRET_KEY if not set.
ATTESTATION_JWT_SECRET = os.getenv('ATTESTATION_JWT_SECRET', '')
ATTESTATION_JWT_TTL_HOURS = int(os.getenv('ATTESTATION_JWT_TTL_HOURS', '24'))
# HS256 keys must be at least 32 bytes (RFC 7518 §3.2); the system check attnd.W001/E001 enforces it.
# Tests run with a short SECRET_KEY, so they get a dedicated test-only secret of the right length.
if TESTING and not ATTESTATION_JWT_SECRET:
    ATTESTATION_JWT_SECRET = 'test-only-attestation-jwt-secret-32-bytes-or-more'

# ---------------------------------------------------------------------------
# Push notifications (see events/push.py)
# ---------------------------------------------------------------------------
APNS_AUTH_KEY = os.getenv('APNS_AUTH_KEY', '')
APNS_KEY_ID = os.getenv('APNS_KEY_ID', '')
APNS_TEAM_ID = os.getenv('APNS_TEAM_ID', '')
APNS_TOPIC = os.getenv('APNS_TOPIC', '')
APNS_CLIP_TOPIC = os.getenv('APNS_CLIP_TOPIC', '')  # API-019: default '<APNS_TOPIC>.clip'
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

