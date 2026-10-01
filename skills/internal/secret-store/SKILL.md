---
name: secret-store
description: "Request any private inputs through a dynamic secure form: tokens, env keys, account fields, passwords, signing/private keys, or other operator values. Store encrypted bundles without returning values to the model; no HTTP connection required."
version: 1.2
---

# Dynamic private input and secret bundles

Use when the task needs user-provided private values. The agent defines the
**names, labels, count, types and purpose** of the fields. No service-specific
field names, URL, protocol or authentication scheme is required by the store.

Never ask users to paste values into chat, `clarify`, commands, memories, or
workspace files. A field rendered as text/textarea/select is **still private**.
All user-entered values are encrypted together; CLI/API results contain only a
bundle handle and metadata. Option definitions are public schema; the selected
option is never separately echoed.

## Request exactly the inputs needed

Run from a **foreground bash tool in an owned web chat**, locally or through
an updated tunnel connector with the `tomo` CLI installed on that host.
Use `bash` timeout **120 seconds** while waiting for the secure form. Tomo gives
the shell a temporary, session-scoped broker capability, not stored private
values. Do not print/persist/copy that capability or read encryption keys/DB.

```bash
tomo secret list
tomo secret request workspace --form '{
  "title": "Private workspace settings",
  "purpose": "Save the private settings needed for the requested task",
  "fields": [
    {"name": "ACCOUNT_NAME", "label": "Account", "type": "text"},
    {"name": "ACCESS_KEY", "label": "Access key", "type": "password"},
    {"name": "REGION", "label": "Region", "type": "select", "options": ["west", "east"]},
    {"name": "SIGNING_KEY", "label": "Signing key", "type": "textarea", "required": false}
  ]
}'
```

Those names are examples, not required fields. Define a different schema for
each task. `--form -` reads schema JSON from stdin. Only **definitions**, never
actual private values, belong in the schema/CLI arguments.

Schema:

- Root: `title` (optional, max 120), `purpose` (optional, max 500), `fields`.
- `fields`: 1–12 objects. Each has a unique `name` (letters/digits/underscore/
  hyphen, starting with a letter or underscore; max 64).
- `label` (optional, max 100), `description` (optional, max 300).
- `type`: `password` (default), `text`, `textarea`, or `select`.
- `required`: boolean, defaults true.
- `select`: `options`, 1–32 non-empty strings, max 120 each.
- No default/value, public/sensitive switch, HTML, JavaScript, executable
  template, regex validator, or arbitrary file upload.
- Single-line values: max 8192 characters. Textarea: multiline, max 32768.
  Optional fields can be blank. Whitespace/newlines inside values are preserved.

The CLI waits up to 90 seconds (`--wait`, max 95). The user sees the purpose and
field descriptions, submits directly to the backend, or cancels. A refresh
restores the definitions, never filled values. Success returns `status: ready`
and a `bundle` with ID/name/field definitions and approved usage metadata.

## Scope and use

Bundles are session-local and owned by that session's user. Reuse a suitable
handle within the chat; requesting the same name again replaces its values and
usage after user input. Revocation:

```bash
tomo secret revoke workspace
```

There is **no plaintext `get`, export, or env injection into agent-controlled
bash**. Saving values does not run a task or deploy an application.

The store is protocol-neutral. Trusted backend consumers read scoped values
internally and must not return them through tool output, exceptions or logs.
`tomo http` requires a separately approved origin and field-to-auth bindings;
load `use_skill(skill_id="secure-http")`. A store-only bundle cannot be used
for HTTP without an approved HTTP policy. The file consumer below applies
stored fields inside the local shell's workspace or the tunnel connector's
configured work root. HTTP executes where the invoking bash runs: backend
locally, connector on a tunnel, so remote-only intranet services are reachable.

## Apply private fields to app configuration

Write non-private configuration first, add generated secret paths to the
project's `.gitignore`, then use the backend to apply stored fields. Neither
CLI arguments nor stdin contain values; mappings contain **names only**.

```bash
# After defining/requesting whichever fields this app actually needs:
tomo secret apply workspace --file .env --format compose \
  --map '{"API_TOKEN":"ACCESS_KEY","APP_ACCOUNT":"ACCOUNT_NAME"}'
tomo secret apply workspace --file signing.pem --format text --field SIGNING_KEY
tomo secret apply workspace --file config.json --format json \
  --map '{"apiKey":"ACCESS_KEY"}'
```

- `--format compose` (default): Docker Compose `.env` / `env_file` syntax.
  Dollars, quotes, backslashes, Unicode, tabs and multiline values are escaped
  for Compose. Unsupported control characters fail rather than corrupt values.
- `--format dotenv`: python-dotenv dialect. The consuming loader must use
  `interpolate=False` to preserve literal `${...}` in secrets; quoting alone
  does not disable python-dotenv interpolation. Do not assume an app's loader
  is compatible without checking its documentation.
- `--format json`: merge private values into top-level object keys, retaining
  unrelated settings. Nested key paths/templates are not supported.
- `--format text`: write one exact field, including newlines (e.g. PEM).
- Without `--map`, env/JSON output keys use the form's field names. Map names
  containing hyphens to valid env keys. `--map -` reads metadata from stdin.
- Paths resolve from CLI cwd and must stay inside the shell's initial local
  workspace or the connector's work root. Create parent directories first. Symlink targets, directories,
  invalid existing env/JSON syntax and files over 1 MB are rejected.
- Env updates replace all occurrences of mapped keys (including `export` and
  multiline assignments), retaining unrelated entries/comments. JSON merges
  top-level keys; text replaces the file. Files are atomically replaced with
  owner-only permissions (0600); errors do not echo values or parser input.
- Repeat apply to rotate/update a file after requesting the bundle again.
  Revoking a bundle does **not** remove values already written to files or
  running applications. Cleanup/redeploy those separately when required.

Success returns only `ok`, the file path, format and key names. Verify that
metadata and app health, **not** file contents or rendered config. Do not run
`cat .env`, print secret files, `docker compose config`, `docker inspect`,
`env`, shell tracing or secret-bearing diffs as part of this workflow. Do not
commit generated secret files. Use `docker compose up -d` separately when the
user authorized deployment; apply itself never executes a command.

There is no universal `.env` dialect. The quoted output is **not** suitable for
shell sourcing or `docker run --env-file`. Choose a documented supported
consumer/format, or report the limitation; never recover values via local code
as a fallback. Other protocols/file consumers are not implemented by a skill.

## Tunnel prerequisites and failure handling

Use the same commands without manually setting broker URLs/tokens. Update both
Tomo and the connector binary; install the matching `tomo` CLI on the remote
host. The connector advertises `secret-broker` and runs a loopback-only bridge
forwarding broker metadata to the paired server's HTTPS endpoint. Private file
and HTTP consumer payloads use authenticated WebSocket RPC, never command text
or agent environment. Server HTTPS/WSS is required (loopback HTTP is allowed
for local tests); do not disable TLS verification. The paired server must allow
both connector WebSocket and broker HTTPS routes through its reverse proxy.

Capabilities are scoped to session/user/workplace and revoked on shell exit,
timeout or session stop. File apply uses the same backend renderer in both
locations and an optimistic content check before atomic remote replacement;
a concurrent edit fails rather than silently overwriting it. Private read/HTTP
replies are cached only in bounded connector memory, not the persistent replay
journal. After a connector restart, an interrupted operation can have uncertain
status: do not blindly retry HTTP mutations or file application. Verify effects
without printing private values first. Revocation still does not erase files.

Missing access on old/insecure/offline tunnels, SSH, background jobs, Telegram,
scheduler, or standalone terminals is not a reason to request raw input.
Report the prerequisite/limitation, never recover credentials through local
code. Cancellation/timeouts are not successful storage; never claim external
verification from `ready` alone.

## Security boundary

Stored maps use Tomo's Fernet encryption. Metadata never includes entered
values, ciphertext, masked previews, or a selected value. The shell capability
expires when its command exits/times out or the session stops.

This is **transport privacy**, not an OS-isolated vault or a filesystem read
ban. Generated files contain plaintext for the app, and ordinary agent tools
still have their existing filesystem access. Current local bash shares the
backend's OS account; cwd guards and 0600 are not isolation. The supported
workflow keeps values out of the model by not reading/printing them.
Strong protection against a malicious executor needs a separate OS/container
boundary without access to the master key/store and restricted consumer access.
Do not promise zero leakage or add plaintext getters as a workaround.
