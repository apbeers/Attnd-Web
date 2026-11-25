"""The MCP protocol layer (official `mcp` SDK, docs/mcp_plan.md): one low-level server whose handlers only translate
between the SDK's types and mcp_server/service.py, and the ASGI app attnd/asgi.py mounts at ``/mcp``.

* Streamable HTTP, **stateless with JSON responses**: every POST is answered on its own (no SSE, no sessions), so
  any web dyno can answer any request and nothing waits on the event loop. The SDK serves every protocol version
  it knows (the 2025-11-25 handshake and the 2026-07-28 single-exchange form).
* **Auth first**: the bearer token is checked (mcp_server/grants.py) before the SDK sees the request. Without a
  usable token the answer is ``401`` with ``WWW-Authenticate: Bearer resource_metadata=… scope="mcp"`` (RFC 9728),
  which is how Claude finds the OAuth server.
* The person is passed to the handlers in a context variable; the Django work runs in a worker thread and gives its
  database connection back afterwards.
"""

import asyncio
import contextvars
import json
import logging
from urllib.parse import urlsplit

import mcp.types as t
from asgiref.sync import sync_to_async
from django.conf import settings
from mcp.server.lowlevel.server import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings

from mcp_server import grants, service

logger = logging.getLogger('attnd.mcp')

PRINCIPAL = contextvars.ContextVar('attnd_mcp_principal', default=None)
APPS_EXTENSION = 'io.modelcontextprotocol/ui'


def _closing(fn):
    """Run Django work off the event loop and give the database connection back to the pool afterwards."""
    def run(*args, **kwargs):
        from django.db import close_old_connections, connections
        if settings.TESTING:  # a test's connection holds its transaction: leave it alone
            return fn(*args, **kwargs)
        close_old_connections()
        try:
            return fn(*args, **kwargs)
        finally:
            connections.close_all()
    return sync_to_async(run, thread_sensitive=settings.TESTING)


def _principal():
    principal = PRINCIPAL.get()
    if principal is None:  # the ASGI app always sets it; a handler without it is a bug, not a client error
        raise RuntimeError('MCP request without a principal')
    return principal


async def list_tools(ctx, params):
    defs = await _closing(service.tools)(_principal().user)
    return t.ListToolsResult.model_validate({'tools': defs})


async def call_tool(ctx, params):
    out = await _closing(service.call)(_principal(), params.name, dict(params.arguments or {}))
    result = {'content': [{'type': 'text', 'text': out['text']}], 'isError': bool(out['is_error'])}
    if out.get('structured') is not None:
        result['structuredContent'] = out['structured']
    return t.CallToolResult.model_validate(result)


async def list_resources(ctx, params):
    return t.ListResourcesResult.model_validate({'resources': service.resources()})


async def read_resource(ctx, params):
    try:
        content = await _closing(service.read_resource)(str(params.uri))
    except service.ToolError as exc:
        from mcp.shared.exceptions import MCPError
        raise MCPError(code=-32002, message=str(exc)) from exc
    return t.ReadResourceResult.model_validate({'contents': [content]})


def build_server():
    server = Server('attnd', version='1', title='Attnd', instructions=service.INSTRUCTIONS,
                    website_url=settings.SITE_URL, on_list_tools=list_tools, on_call_tool=call_tool,
                    on_list_resources=list_resources, on_read_resource=read_resource)
    server.extensions = {APPS_EXTENSION: {'mimeTypes': [service.APP_MIME_TYPE]}}
    return server


def _security():
    host = urlsplit(settings.SITE_URL).netloc
    hosts = [host]
    if settings.DEBUG or settings.TESTING:
        hosts += ['localhost:*', '127.0.0.1:*', 'testserver', 'localhost', '127.0.0.1']
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True, allowed_hosts=hosts,
        allowed_origins=[settings.SITE_URL, 'https://claude.ai', 'https://claude.com'])


def challenge(error=''):
    """The 401's WWW-Authenticate value (RFC 6750 + RFC 9728)."""
    metadata = settings.SITE_URL + '/.well-known/oauth-protected-resource' + urlsplit(settings.MCP_RESOURCE_URL).path
    parts = [f'resource_metadata="{metadata}"', 'scope="mcp"']
    if error:
        parts.insert(0, f'error="{error}"')
    return 'Bearer ' + ', '.join(parts)


async def _json(send, status, body, headers=()):
    data = json.dumps(body).encode()
    await send({'type': 'http.response.start', 'status': status,
                'headers': [(b'content-type', b'application/json'), (b'content-length', str(len(data)).encode()),
                            (b'cache-control', b'no-store'), *headers]})
    await send({'type': 'http.response.body', 'body': data})


class McpApp:
    """ASGI app for ``/mcp``: auth, then the SDK's stateless Streamable HTTP handler."""

    def __init__(self):
        self.manager = None
        self._ready = None

    async def _keep_running(self):
        async with self.manager.run():
            self._ready.set()
            await asyncio.Event().wait()  # for the life of the process

    async def _started(self):
        if self.manager is None:
            self.manager = StreamableHTTPSessionManager(app=build_server(), json_response=True, stateless=True,
                                                        security_settings=_security())
            self._ready = asyncio.Event()
            self._task = asyncio.get_running_loop().create_task(self._keep_running())
        await self._ready.wait()

    async def __call__(self, scope, receive, send):
        if not settings.MCP_ENABLED:
            return await _json(send, 404, {'error': 'not_found'})
        headers = dict(scope.get('headers') or [])
        auth = headers.get(b'authorization', b'').decode('latin-1')
        token = auth[7:].strip() if auth[:7].lower() == 'bearer ' else ''
        principal = await _closing(grants.principal)(token) if token else None
        if principal is None:
            return await _json(send, 401, {'error': 'invalid_token' if token else 'unauthorized',
                                           'error_description': 'Connect to Attnd first (OAuth).'},
                               headers=[(b'www-authenticate', challenge('invalid_token' if token else '').encode())])
        await self._started()
        reset = PRINCIPAL.set(principal)
        try:
            await self.manager.handle_request(scope, receive, send)
        finally:
            PRINCIPAL.reset(reset)


class Router:
    """In front of the site's ASGI app: ``/mcp`` goes to the MCP server, everything else to Django as before."""

    def __init__(self, app, mcp_app=None):
        self.app = app
        self.mcp = mcp_app or McpApp()

    def __getattr__(self, name):  # the wrapped app's own attributes (e.g. the long-poll waiters in tests)
        return getattr(self.app, name)

    async def __call__(self, scope, receive, send):
        if scope['type'] == 'http' and scope.get('path') in ('/mcp', '/mcp/'):
            return await self.mcp(scope, receive, send)
        return await self.app(scope, receive, send)
