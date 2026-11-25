"""django-oauth-toolkit's validator with the MCP grant rules (docs/mcp_plan.md, mcp_server/grants.py).

* The first token pair of a code exchange starts a grant (its own session); refreshes stay in that grant's family.
* A refresh only works while the grant is usable (person still staff and active, session still theirs, younger
  than the maximum age); otherwise the grant is revoked and the client has to connect again.
* Only this server's MCP URL may be named as the resource (RFC 8707).
"""

from django.conf import settings
from oauth2_provider.oauth2_validators import OAuth2Validator
from oauthlib.oauth2.rfc6749 import errors

from mcp_server import grants


class Validator(OAuth2Validator):
    def _check_and_set_request_resource(self, request):
        super()._check_and_set_request_resource(request)
        wanted = settings.MCP_RESOURCE_URL.rstrip('/')
        for resource in getattr(request, 'resource', None) or []:
            if str(resource).rstrip('/') != wanted:
                raise errors.CustomOAuth2Error(
                    error='invalid_target', description=f'Only {wanted} can be requested here.', request=request)

    def _create_refresh_token(self, request, refresh_token_code, access_token, previous_refresh_token):
        refresh = super()._create_refresh_token(request, refresh_token_code, access_token, previous_refresh_token)
        if previous_refresh_token is None:  # a code exchange: a new connection
            grants.start(request.user, request.client, refresh.token_family, getattr(request, 'redirect_uri', ''))
        return refresh

    def validate_refresh_token(self, refresh_token, client, request, *args, **kwargs):
        if not super().validate_refresh_token(refresh_token, client, request, *args, **kwargs):
            return False
        instance = getattr(request, 'refresh_token_instance', None)
        grant = grants.for_family(instance.token_family) if instance is not None else None
        return grant is not None and grants.usable(grant)
