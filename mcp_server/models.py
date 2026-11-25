"""MCP clients connected through OAuth (docs/mcp_plan.md).

A grant is one connection of an MCP client (e.g. Claude) to a person's account: created when the client first
exchanges its authorization code, kept across refresh-token rotations (one token family), ended by Revoke, by the
person losing the permission, by a password change or sign-out everywhere (its session goes), or at its maximum age.
"""

from django.conf import settings
from django.db import models
from django.utils import timezone


class McpGrant(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='mcp_grants')
    application = models.ForeignKey(settings.OAUTH2_PROVIDER_APPLICATION_MODEL, on_delete=models.CASCADE,
                                    related_name='+')
    token_family = models.UUIDField(unique=True)  # django-oauth-toolkit's refresh-token family
    # A Django session of its own, signed in as the person: the runner (agent/internal.py) uses it read-only, so
    # the two-factor, permission and password checks are exactly the console's. It outlives the browser session.
    session_key = models.CharField(max_length=64)
    client_name = models.CharField(max_length=120, blank=True)
    redirect_host = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    last_used_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoked_reason = models.CharField(max_length=120, blank=True)

    class Meta:
        ordering = ['-created_at']
        permissions = [('use_mcp', 'Use Attnd from an AI assistant (MCP)'),
                       ('manage_mcp', "Manage everyone's MCP connections")]

    def __str__(self):
        return f'{self.client_name or "MCP client"} for {self.user}'

    @property
    def active(self):
        return self.revoked_at is None
