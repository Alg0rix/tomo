---
name: secure-http
description: "Connect to authenticated HTTP APIs (Zabbix, monitoring, SaaS, internal services) without asking for credentials in chat. Use Tomo's secure connection form and curl-like tomo http CLI."
version: 1.1
---

# Secure HTTP connections

Use this whenever an API needs a token/API key. API-specific skills still own
request schemas; this skill handles credential input and authenticated transport.
Never ask users to paste a credential into chat, `clarify`, a command, or a file.
Never use `tomo config` to retrieve credentials, decrypt the store, or print env.

## Execution location

These commands run from a **foreground bash tool in an owned web chat**,
locally or through an updated tunnel connector. Tomo injects a temporary broker
capability automatically; on tunnels it also binds the selected workplace.
It is not an upstream API credential and cannot read credential values. It
expires when that shell exits, times out, or the chat is stopped. Do not print,
persist, or copy it. A later bash call gets fresh access to the same session.

On a tunnel, HTTP executes on the connector host, including its DNS/network
access to intranet services. Install the matching `tomo` CLI there and update
the connector binary (`secret-broker` capability). Its loopback bridge forwards
broker metadata to the paired HTTPS server; private HTTP inputs travel over
connector WebSocket RPC. HTTPS/WSS is required except for loopback tests.
No manual token/URL copying is needed. Offline/old/insecure connectors fail;
do not silently fall back to the backend's network.

SSH workplaces, background commands, Telegram, scheduled/standalone execution
still do not receive broker access. Do not work around missing access with raw
credentials or an admin API key. Report the prerequisite/limitation instead.

## Secret store versus HTTP usage

The store is generic. Load `use_skill(skill_id="secret-store")` to request any
private fields without a URL/protocol/auth scheme. HTTP is only a backend
consumer. Store-only bundles cannot automatically authorize HTTP requests.

## Request a connection

First inspect metadata:

```bash
tomo connection list
```

Reuse a suitable connection from this session. If missing, establish the
non-secret origin and auth scheme from docs/user context, then request it:

```bash
tomo connection request zabbix --url https://zabbix.example --auth bearer
```

**Set the enclosing `bash` tool timeout to 120 seconds** for secure input. The
CLI waits up to 90 seconds for the form; it returns only status and approved
connection metadata. The user can review/change the proposed origin/auth and
enter the credential in a password field sent directly to the backend.

### Dynamic fields and bindings

To ask for different fields, supply a metadata-only `--form` (or `--form -`
for stdin). Its field schema is the same as `secret-store`, plus an `auth`
object declaring which private field names the HTTP consumer uses:

```bash
tomo connection request vendor --url https://api.example --form '{
  "title": "Private API access", "purpose": "Access the requested API",
  "fields": [
    {"name": "API_KEY", "label": "API key", "type": "password"},
    {"name": "ACCOUNT_ID", "label": "Account ID", "type": "text"}
  ],
  "auth": {"type": "header", "fields": {"X-API-Key": "API_KEY", "X-Account-ID": "ACCOUNT_ID"}}
}'
```

Field names are arbitrary; no values belong in this definition. Auth bindings:

- `bearer`: `token_field` references a private field.
- `basic`: `username_field` and `password_field` reference two private fields.
- `header`: `fields` maps header names to private field names.
- `json`: `fields` maps top-level JSON body keys to private field names.

This is transport configuration, not a service-specific integration or login
workflow. Arbitrary login scripts/token exchanges are not implemented. All
entered fields stay private, including usernames and selected options; known
echoes of any entered value and Basic-auth combinations are withheld.

Convenience presets (optional; custom schemas need not use these field names):

```bash
tomo connection request vendor --url https://api.example --auth header --auth-field X-API-Key
tomo connection request legacy-api --url https://api.example --auth json --auth-field auth
tomo connection request basic-api --url https://api.example --auth basic
```

- `bearer`: backend injects `Authorization: Bearer <credential>`.
- `header`: backend injects the named API-key header.
- `json`: backend injects/overwrites the named **top-level** JSON field.
- `basic`: the convenience preset requests username/password, both private.
- HTTPS is preferred. HTTP requires explicit user consent in the form because
  it sends credentials unencrypted. Never disable TLS verification.
- Timeout/cancel is not a successful connection. Retry the secure form only
  when appropriate; never ask for the token in chat.
- Connections are **session-local and execution-location-bound**, not global
  names. The form shows the backend or tunnel workplace that will execute HTTP.
  A backend-approved `localhost` origin must not become a remote `localhost`
  destination (or vice versa); request approval at the current location instead.
  A new chat needs its own connection. Requesting the same name replaces it.

## Make requests

Use `tomo http`, not curl with a token copied from env/store. Example JSON-RPC:

```bash
tomo http --connection zabbix -X POST /api_jsonrpc.php \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","method":"host.get","params":{"output":["hostid","host"]},"id":1}'
```

Syntax:

```text
tomo http --connection NAME_OR_ID [-X METHOD] [-H 'Accept: …']
          [-H 'Content-Type: …'] [-d TEXT_OR_JSON | -d -] [--timeout SECONDS] /path?query
```

`-d -` reads stdin. No `@file`, arbitrary auth headers, full URLs, `-k`, redirect
following, cookies, or proxy switches. Method defaults to GET, or POST with a
body. Upstream timeout is at most 60 seconds; keep the bash timeout longer than
it. Request bodies are limited to 1 MB and responses to 2 MB. The CLI prints
response text; exit 22 means HTTP >=300, exit 1 means broker/input failure.
Inspect both HTTP status and API-level errors (e.g. JSON-RPC `error`). A saved
credential does not prove the API works; verify a small request before reporting
success. Redirects are refused, not silently followed.

The connection fixes the origin, not allowed API operations. Use a least-
privilege/read-only upstream token where possible. Obtain user authorization
before mutations; a POST can be a read (Zabbix JSON-RPC), not necessarily a write.

Revoke when requested:

```bash
tomo connection revoke zabbix
```

There is deliberately no plaintext `get`/export command. Do not put credentials
in skill files, memories, artifacts, tool arguments, or final answers.

## Security boundary (do not overclaim)

The CLI never opens the secret database or decrypts credentials. The backend
validates the approved origin/auth bindings for local and tunnel requests.
Local `httpx` or the remote connector's HTTP consumer sends the request, with
no upstream environment proxies or redirects. Known private echoes are withheld
by the backend before reaching the CLI. Connector private HTTP/read replies are
never written to its replay journal; after restart, interrupted operations may
have uncertain status, so never blindly repeat a mutation. Stored credentials use Tomo's existing Fernet encryption. Response
headers and authenticated exception details are not returned; responses echoing
known raw/URL-encoded/JSON-escaped/base64 credential forms are withheld.

This prevents plaintext entering normal agent/tool/history paths, **not every
possible exfiltration**. A trusted upstream can transform/echo credentials in
unrecognized ways. More importantly, local bash currently shares the backend's
OS account; cwd/path guards are not an OS security boundary. A malicious shell
can access storage/master keys. Strong isolation requires an executor with a
separate OS/container boundary, without mounts/env/process access to the store
or keys, plus restricted broker access. Do not describe this feature as a fully
isolated vault or guarantee zero leakage on current local deployments.
