# MCP server for the platform (plan)

Status: built and on develop (Nov 25, 2026): OAuth, grants, /mcp, confirm page, Connected apps, the event overview
MCP App and the Prospects tools. Dev has `MCP_ENABLED=1`; production stays off until the owner signs off. The
current architecture is in CLAUDE.md → MCP server; this file keeps the decisions.

## Goal

Let staff run Attnd from Claude (on the web, Desktop, mobile and Cowork, and Claude Code) through a remote MCP server
with OAuth. Staff only at first. Dev and prod each get their own server, connector and data.

## Decisions

- **Audience:** Staff (the Staff group). Platform admins can see more later.
- **Scope:** start small. A tool is exposed only when its registry action has `mcp=True`.
- **Order of tool groups:** events overview (read, then a `ui://` card), Prospects read, then Prospects writes.
- **Writes:** every write that the chat would confirm also waits for a Confirm in Ask Attnd. MCP never confirms.
- **Chat and MCP are parallel.** The chat keeps its own model loop (`agent/loop.py`). MCP has no model. They share
  the tool list (`agent/tools.py`), the runner (`agent/internal.py`), the confirm gate and the run record.
- **Refresh outlives the browser session** (owner decision). A grant keeps its own principal: the person, the time of
  their last full sign-in (`auth_at`) and the grant limits. The grant does not depend on a Django session.
- **Sensitive actions** (the `recent_auth` rule, 20 minutes) still need a fresh sign-in in the browser.
- **Permissions are checked on every call**, so losing staff status stops MCP access at once.
- **Libraries** (owner: latest versions, pinned, bumped with `scripts/upgrade_deps.py`):
  - `mcp` 2.3.0 for the protocol, tools and MCP Apps resources (PyPI, Oct 2, 2026).
  - `django-oauth-toolkit` 3.4.1 for the authorisation server (PyPI, Aug 21, 2026).
  Both are approved exceptions to the dependency policy in CLAUDE.md.

## Environments

| | Dev | Prod |
|---|---|---|
| MCP server | `https://dev.attnd.app/mcp` | `https://attnd.app/mcp` |
| Authorisation server | `dev.attnd.app` | `attnd.app` |
| Claude connector | "Attnd (dev)" | "Attnd" |
| Grants and tokens | Dev database | Prod database |
| Signing and hash secrets | Dev environment variables | Prod environment variables |
| Data | DEMO event, showcase events, test prospects | Real organizers and prospects |
| Enabled | `MCP_ENABLED` on (testing) | `MCP_ENABLED` off until the owner signs off |

- Same code; everything derives from `SITE_URL`. No environment branches in the code.
- Each access token is bound to its own server URL (RFC 8707). A dev token is refused on prod.
- The Connected apps page shows a "Dev" banner on dev.

## OAuth

- Authorisation code with PKCE (S256 only); resource indicator bound to the server URL.
- Clients: Claude's callback `https://claude.ai/api/mcp/auth_callback` (confirmed in Claude's connector docs,
  https://claude.com/docs/connectors/building/authentication.md). Claude Code uses a loopback redirect on any port
  (`localhost` and `127.0.0.1`, port ignored) and a Client ID Metadata Document. Accept metadata documents, with a
  short allow-list as the fallback. Open dynamic registration stays off.
- Access tokens last 1 hour. Refresh tokens rotate on every use, with reuse detection (a reused token revokes the
  whole grant).
- Idle limit 30 days; maximum grant life 90 days, then re-consent. (Owner to confirm the numbers.)
- Token storage: SHA-256 hashes only, with a separate secret.
- Revoke on the Connected apps page. "Sign out everywhere" and a password or passkey change revoke all grants.
- The consent screen is behind sign-in (so 2FA applies) and shows the redirect's hostname clearly (MCP spec
  2025-11-25, authorisation).

## Endpoints

| Path | Purpose | Auth |
|---|---|---|
| `/.well-known/oauth-protected-resource` | Where the authorisation server is | Public |
| `/.well-known/oauth-authorization-server` | Authorisation server metadata | Public |
| `/oauth/authorize/` | Consent after sign-in | Signed in |
| `/oauth/token/` | Code exchange, refresh | Client authentication |
| `/oauth/revoke/` | Revocation | Client authentication |
| `/mcp` | MCP endpoint (stateless Streamable HTTP, JSON responses; ASGI only) | Bearer token |
| `/console/mcp/confirm/<step>/` | The person confirms or cancels an always-ask action | Signed in |

All are declared public for `LoginRequiredMiddleware` and listed in `attnd/tests_public.py`. Token and MCP endpoints
are CSRF-exempt. Rate limits apply to the token endpoint and per token on `/mcp`.

## Layers

```
Clients     Ask Attnd chat · Claude (MCP) · the MCP App (iframe, rendered by Claude)
Transport   agent views · mcp/ (SDK) · oauth/ (django-oauth-toolkit)
Tools       agent/tools.py: name, schema, permission, confirm, audience, mcp flag
Runner      agent/internal.py, as a principal (session for the chat; grant principal for MCP)
Components  console/components: data function (JSON) → chat, canvas, and MCP App renderers
Domain      events · scrape · studio · credit · prospects
```

- The chat and MCP never import views or write models for writes. They use the registry and the runner.
- The API v2 (devices) keeps its own auth and is not reused.

## Phases

1. **Shared layer.** Tool list in `agent/tools.py` (done). The runner needed no change: each grant gets its own
   Django session, which the runner uses like the chat uses the browser's.
2. **OAuth** (`mcp_server/`, one app with the MCP server; the SDK's top-level package is also called `mcp`): DOT, PKCE, rotation, revocation, metadata, consent page, client metadata documents.
3. **MCP server** (`mcp_server/server.py`): initialise, tools, ping, 401 with `WWW-Authenticate`. Text results only.
4. **Runs and confirm:** `AgentRun.source` (`chat` / `mcp`), migration, MCP writes waiting for confirmation in Ask Attnd.
5. **Connected apps** (Platform sub-tab, staff): grants, revoke (asks), clients (platform admins), the `mcp` tool list,
   recent MCP runs. Each one is a component, so the chat and page parity test covers them.
6. **MCP Apps:** the events overview card (`ui://`), reusing the overview component; buttons call tools through the host.
   Check the current MCP Apps spec before building.
7. **Prospects:** read tools (pipeline, detail with do-not-contact redaction, runs, settings), then write tools (status,
   notes, activities; "create test event", discovery and claim decisions always ask). No send tool: Attnd never emails
   prospects. Discovery counts toward `PROSPECT_DAILY_SPEND_CAP_USD`.
8. **Dev rollout** with the "Attnd (dev)" connector. Prod stays off until the owner signs off.

## Tests

- Scripted MCP client: initialise, list, call, errors, 401s, Origin checks.
- OAuth: PKCE, redirect matching (port-agnostic loopback), rotation and reuse detection, revocation, audience binding,
  expiry, and the session-independent refresh.
- Registry: every `mcp=True` tool has a permission and an audience; the chat's tool list is unchanged.
- Permission and public-view tests (`attnd/tests_permissions.py`, `attnd/tests_public.py`) cover the new views.
- Prospects: do-not-contact contacts never show an email or phone through MCP.

## Open items

- Confirm the idle (30 days) and maximum (90 days) grant lifetimes.
- The MCP App's host bridge (ui/initialize, tool-result notifications) is written from the MCP Apps spec and untested
  in Claude itself: check it with the dev connector.
- Production: set `MCP_ENABLED=1` on attnd-web after the owner signs off.
- Later: more tools (event writes, discovery runs), more MCP Apps (schedule, Prospects pipeline).
