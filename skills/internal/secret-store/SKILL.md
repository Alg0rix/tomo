---
name: secret-store
description: "Request any private inputs through a dynamic secure form: tokens, env keys, account fields, passwords, signing/private keys, or other operator values. Store encrypted bundles without returning values to the model; no HTTP connection required."
version: 1.0
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

Run from a **foreground local coordinator bash tool in an owned web chat**.
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
bash**. Saving values does not run a task or automatically grant a consumer.

The store is protocol-neutral. A trusted backend consumer receives a scoped
bundle reference, checks its approved usage, and reads values internally. It
must not return them through tool output, exceptions, logs or artifacts. Merely
adding a skill does not create a safe executable consumer.

The currently available consumer is `tomo http`; load
`use_skill(skill_id="secure-http")` for its separately approved origin and
field-to-auth bindings. A store-only bundle cannot be used for HTTP without an
approved HTTP policy. Other consumers are not implemented yet: report that
limitation, do not invent a command or recover values via local code.

Missing broker access on SSH/tunnel, background jobs, Telegram, scheduler, or a
standalone terminal is not a reason to request raw input. Use the supported
local web-chat path or report the limitation. Cancellation/timeouts are not
successful storage; never claim external verification from `ready` alone.

## Security boundary

Stored maps use Tomo's Fernet encryption. Metadata never includes entered
values, ciphertext, masked previews, or a selected value. The shell capability
expires when its command exits/times out or the session stops.

This is not an OS-isolated vault. Current local bash shares the backend's OS
account and can access its filesystem/processes; cwd guards are not isolation.
Strong protection against a malicious executor needs a separate OS/container
boundary without access to the master key/store and restricted consumer access.
Do not promise zero leakage or add plaintext getters as a workaround.
