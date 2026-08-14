# MCP Connector Design

**Date:** 2026-08-14
**Status:** Approved design; implementation plan pending

## Goal

Add a first-class Model Context Protocol (MCP) connector to Tomo. An admin can
configure local `stdio` servers and remote Streamable HTTP servers from System,
discover their tools, resources, and prompts, enable or disable a server or
individual capability, and assign discovered tools per agent.

The implementation uses the official Python MCP SDK. The two selected
transports are the current standard `stdio` and Streamable HTTP transports;
legacy HTTP+SSE is not part of this feature.

## Scope

### In scope

- MCP server CRUD in System → MCP.
- Local `stdio` command, argument, and environment configuration.
- Remote Streamable HTTP URL and custom header configuration.
- Encrypted storage for environment and header values.
- Immediate connection and capability discovery after saving an enabled server.
- Manual refresh/test and visible connection status/errors.
- Discovery and storage of paginated tools, resources, resource templates, and
  prompts.
- Global server enable/disable.
- Global per-capability enable/disable.
- Per-agent enable/disable for MCP tools using Tomo's existing `agent_tools`
  mechanism.
- Tool calls routed through Tomo's existing approval, event, and error paths.
- Resource read/preview/insertion actions and prompt selection/insertion from
  the chat UI.
- Unit and integration tests using local fake MCP servers; no external service
  is required for the test suite.

### Out of scope

- OAuth discovery, browser authorization, token refresh, or mTLS UI.
- The legacy HTTP+SSE transport.
- MCP sampling, elicitation, roots, subscriptions, and server-initiated
  client requests.
- Automatically injecting server instructions or discovered resources into
  every conversation.
- Replacing Tomo's existing built-in tool registry or permission model.

## Architecture

### Connection manager

Create `app/runtime/mcp/` with a lifespan-managed `McpConnectionManager`.
The manager owns live SDK sessions in memory and keeps one session per enabled
server. Each server has an async lock so discovery, reconnect, and calls do not
race one another. The manager closes all sessions during application shutdown.

The manager uses the official SDK transport adapters:

- `stdio`: launch a configured subprocess using an argument vector, never a
  shell command string. Pass only the SDK's minimal environment plus the
  explicitly configured environment values.
- Streamable HTTP: connect to one configured MCP endpoint with a client that
  carries the configured custom headers.

Saving an enabled server commits its configuration first, then connects and
discovers capabilities. A connection failure leaves the configuration saved,
sets the server status to `error`, and returns the status and safe error text
to the UI. Invalid configuration is rejected before persistence. Disabled
servers are saved without opening a connection.

After a process restart, cached discovery remains available for the UI. Before
each agent turn, the runtime lazily reconnects enabled servers that have cached
MCP items, so only live tools are advertised for that turn. A closed session is
retried once; repeated failure marks the server `error` and omits its tools
from that turn. Manual refresh always attempts a fresh connection and
discovery.

### Runtime integration

Built-in tools continue to use the existing synchronous registry. Add an async
registry dispatcher for MCP calls:

1. Built-in calls run in the existing worker-thread path.
2. Namespaced MCP calls resolve to the owning server and original MCP tool
   name, then call `tools/call` through the live SDK session.
3. The dispatcher rechecks server and item enablement immediately before the
   call, so a stale model call cannot bypass a newly-disabled setting.

Update the main agent loop, delegate path, and ATG executor to use the async
dispatcher. Background learning keeps its existing explicit built-in-only
allowlist and does not receive arbitrary MCP tools.

MCP tool results are converted to Tomo's string tool-result contract while
preserving text blocks, structured JSON, resource links, and safe summaries for
binary content. MCP `isError` results become Tomo tool errors.

## Persistence model

### `mcp_servers`

Add a SQLite table with the following fields:

| Field | Purpose |
| --- | --- |
| `id` | Stable URL-safe server identifier and primary key |
| `name` | User-facing display name |
| `transport` | `stdio` or `streamable_http` |
| `command` | `stdio` executable |
| `args_json` | JSON array of `stdio` arguments |
| `url` | Streamable HTTP endpoint |
| `env_ciphertext` | Encrypted JSON object of `stdio` environment values |
| `headers_ciphertext` | Encrypted JSON object of HTTP header values |
| `enabled` | Global server switch |
| `status` | `unknown`, `connected`, `error`, or `disabled` |
| `status_message` | Safe, non-secret status/error text |
| `server_info_json` | Last MCP server information snapshot |
| `capabilities_json` | Last negotiated capability snapshot |
| `last_connected_at` | Successful connection timestamp |
| `last_discovered_at` | Successful discovery timestamp |
| `created_at` / `updated_at` | Record timestamps |

### `mcp_items`

Add one table for all discovered capabilities:

| Field | Purpose |
| --- | --- |
| `id` | Stable internal capability identifier and primary key |
| `server_id` | Foreign key to `mcp_servers` with cascade delete |
| `kind` | `tool`, `resource`, `resource_template`, or `prompt` |
| `runtime_id` | Namespaced Tomo tool ID for tools; empty for other kinds |
| `name` | Original MCP name |
| `title` / `description` | Display metadata |
| `uri` | Resource or template URI |
| `mime_type` | Resource MIME type |
| `schema_json` | Tool input schema or prompt argument schema |
| `metadata_json` | Remaining non-secret MCP metadata |
| `enabled` | Global item switch |
| `created_at` / `updated_at` | Record timestamps |

Use a uniqueness constraint over `(server_id, kind, name, uri)`. Refresh
upserts current records and removes stale records for that server. Tool runtime
IDs use a deterministic `mcp__<server>__<tool>` form, normalized and shortened
with a stable hash if necessary to stay within provider tool-name limits.

Existing `agent_tools` rows store those runtime IDs. Built-in and MCP tools are
merged when rendering Agent Studio → Tools and when constructing the agent's
OpenAI-compatible tool schema list. A missing row keeps Tomo's existing
default-enabled behavior; an explicit saved map records every known tool.

All environment and header values are encrypted with the existing Fernet
helpers. Public views return only key/header names and configured flags. On
update, omitted secret maps preserve their existing ciphertext; supplied maps
replace the corresponding map, with masked values accepted as “keep existing”.

## API

Add authenticated platform routes:

- `GET /api/mcp-servers` — list safe server summaries.
- `POST /api/mcp-servers` — validate, save, and immediately connect/discover
  when enabled.
- `GET /api/mcp-servers/{server_id}` — safe server detail plus capability
  counts and discovered items.
- `PUT /api/mcp-servers/{server_id}` — update configuration or enabled state;
  reconnect/discover when an enabled configuration changes.
- `DELETE /api/mcp-servers/{server_id}` — close the session and delete the
  server and its discovered items.
- `POST /api/mcp-servers/{server_id}/refresh` — reconnect and rediscover.
- `PUT /api/mcp-servers/{server_id}/items/{item_id}` — update one global item
  enablement flag.
- `GET /api/mcp-servers/{server_id}/resources` — list enabled resources and
  templates.
- `POST /api/mcp-servers/{server_id}/resources/read` — read a selected URI.
- `GET /api/mcp-servers/{server_id}/prompts` — list enabled prompts.
- `POST /api/mcp-servers/{server_id}/prompts/get` — resolve a prompt with
  user-supplied arguments.

The existing `PUT /api/agents/{agent_id}/tools` accepts namespaced MCP tool
IDs without a new per-agent endpoint. Requests for unknown servers/items return
HTTP 404; requests against a disabled server or item return HTTP 409 with a
safe explanatory detail. The runtime repeats the same check for defense in
depth.

## UI

Add a System navigation entry and section named MCP. A server row shows its
transport, status, enabled switch, discovered counts, last discovery time, and
Edit/Refresh/Delete actions.

The editor shows transport-specific fields:

- `stdio`: command, one argument per row, and environment key/value rows.
- Streamable HTTP: endpoint URL and custom header key/value rows.

Secret inputs are password fields with masked configured-state help text. The
form does not echo stored values back into HTML.

The server detail view has Tools, Resources, and Prompts panels. Tool rows show
the friendly original name, description, schema summary, global enablement, and
the number of agents receiving the tool. Resource and prompt rows expose their
global enablement plus read/use actions.

Extend the existing Agent Studio Tools panel with discovered MCP tools. Show
the owning server as a source badge and disable the per-agent toggle when the
server or global item is off.

Extend the existing slash menu with MCP prompts. Selecting a prompt collects
required arguments, calls `prompts/get`, flattens supported text messages, and
places the result into the composer for user review; it never auto-sends.
Add a resource picker/action that calls `resources/read`. Text resources can be
inserted into the composer as a clearly-marked context block. Binary resources
are previewed or offered for download and are not silently inserted into the
LLM context.

## Safety and permissions

MCP server metadata, tool annotations, resource contents, and prompt contents
are untrusted input. Do not inject server instructions into Tomo's system
prompt. Do not infer permission grants from MCP annotations.

MCP tool calls go through the existing approval pipeline. The permission
assessor adds an external-MCP finding for namespaced calls so Manual and Smart
modes can request consent; Auto mode follows the user's existing explicit
override. MCP calls receive no filesystem outside-jail grant. The UI retains
the existing tool-call and tool-result event indicators.

Do not log command environment values, HTTP header values, prompt arguments, or
raw exception payloads that may contain secrets. Bound stored metadata and
rendered results using the same tool-result limits used by the agent loop.

## Errors and lifecycle states

The server status model is:

- `unknown`: saved but not yet connected or discovered.
- `connected`: live session available and latest discovery succeeded.
- `error`: configuration is retained but the last connection/discovery/call
  failed; the safe error message is shown with Refresh available.
- `disabled`: global switch is off and no live session is retained.

A discovery failure does not silently advertise new or stale tools as usable.
Cached items remain visible for repair, but after the pre-turn reconnect step
only connected, globally enabled, globally item-enabled tools are returned to
agent tool schemas. A call failure returns a bounded `Error: MCP server …`
result and marks the connection stale for the next turn's reconnect attempt.

## Verification

Add tests for:

1. Schema migration and idempotency for both MCP tables.
2. Secret encryption, masking, update-preservation, and no plaintext storage.
3. Server CRUD and safe API response shapes.
4. Immediate discovery, pagination, refresh, stale-item cleanup, and status
   transitions.
5. `stdio` and Streamable HTTP connections against local fake MCP servers.
6. Tool namespacing, OpenAI schema conversion, global filtering, and per-agent
   filtering.
7. Async dispatch, result formatting, reconnect-once behavior, and disabled
   server/item enforcement.
8. Resource read and prompt get actions, including composer-safe text handling.
9. Permission findings for MCP calls and preservation of existing built-in
   tool behavior.

Run the focused MCP tests, the full pytest suite, and the repository's Ruff
checks before declaring the feature complete.
