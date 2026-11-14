"""System checks for API secrets (run by every manage.py command; the errors with --deploy)."""
from django.conf import settings
from django.core.checks import Error, Tags, Warning, register

JWT_SECRET_MIN_BYTES = 32  # HS256 key length (RFC 7518 §3.2)


def _jwt_secret():
    return getattr(settings, 'ATTESTATION_JWT_SECRET', '') or settings.SECRET_KEY


def _short_jwt_secret():
    return len(_jwt_secret().encode()) < JWT_SECRET_MIN_BYTES


_JWT_MSG = (f'The attestation JWT secret (ATTESTATION_JWT_SECRET, or SECRET_KEY when unset) is shorter than '
            f'{JWT_SECRET_MIN_BYTES} bytes.')
_JWT_HINT = 'Set ATTESTATION_JWT_SECRET to e.g. python3 -c "import secrets;print(secrets.token_urlsafe(48))".'


@register(Tags.security)
def attestation_jwt_secret(app_configs, **kwargs):
    """Warn at startup outside DEBUG (a release still deploys); `check --deploy` makes it an error."""
    if settings.DEBUG or not _short_jwt_secret():
        return []
    return [Warning(_JWT_MSG, hint=_JWT_HINT, id='attnd.W001')]


@register(Tags.security, deploy=True)
def attestation_jwt_secret_deploy(app_configs, **kwargs):
    if not _short_jwt_secret():
        return []
    return [Error(_JWT_MSG, hint=_JWT_HINT, id='attnd.E001')]


@register(Tags.security)
def ci_api_keys(app_configs, **kwargs):
    """CI keys (API-008) shorter than 32 characters are ignored by the API; say so."""
    from events.apis import CI_KEY_MIN_LENGTH
    short = [k for k in getattr(settings, 'CI_API_KEYS', []) if len(k) < CI_KEY_MIN_LENGTH]
    if not short:
        return []
    return [Warning(f'{len(short)} CI_API_KEYS entr{"y is" if len(short) == 1 else "ies are"} shorter than '
                    f'{CI_KEY_MIN_LENGTH} characters and will be ignored.',
                    hint='python3 -c "import secrets;print(secrets.token_urlsafe(32))"', id='attnd.W002')]
