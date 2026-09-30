# Configure Tomo from the terminal

## Pick the correct installation

Run on the coordinator under its OS account and with the same `TOMO_HOME` and
`TOMO_DB_PATH`. `tomo config` uses existing model functions and SQLite directly;
it does not call Tomo over HTTP, require an account API key, or start the server.
A missing database is an installation/context error, not a reason to seed a new
one, install another coordinator, or work around it with raw SQL.

Use `tomo --help` and `tomo config --help` to check the installed command set.
For a source checkout, use `uv run python -m cli ...` from that checkout. For
Docker, run `python -m cli ...` inside the actual coordinator container with its
existing data volumes; host defaults refer to a different installation. See
[install paths](install.md) when the deployment method is unclear.

A selected tunnel/SSH workplace can route shell commands to the target. Confirm
that configuration commands execute on the coordinator before making changes.
CLI access uses the OS user's existing access to the installation's data; it
does not imply access to another deployment or remote account.

## Discover, change, read back

```bash
tomo config agents list --json
tomo config agents schema --json
tomo config agents show <actual-agent-id> --json
tomo config agents update <actual-agent-id> --set enabled=false --json
```

Use IDs returned by `list`/`create`, not a guessed name-derived slug. If several
records match the user's name, inspect their host/profile/owner fields before
choosing. Reuse the intended resource for repairs and retries. New resource
creation is not an idempotent upsert.

Use `--set FIELD=VALUE` for simple changes; repeat it for multiple fields. Values
parse as JSON when valid and otherwise remain strings. Use `true`/`false` for
booleans and JSON arrays/objects for structured values. Hyphens in field names
normalize to underscores. Do not supply display labels as field names.

`--data` accepts a JSON object, `@/absolute/file.json`, or `-` for stdin. `--set`
overrides fields loaded through `--data`. Prefer stdin or an existing protected
file for credentials; do not expose them in shell history, command arguments,
chat, or logs. `--json` produces machine-readable output on stdout; errors go to
stderr and return exit code 1. Help/schema are read-only; config reads show
public/masked views. API-key creation intentionally returns its new token once.

Read back the affected fields after a successful write. Stop after demonstrated
success; when a mutation's result is ambiguous, inspect current state before
repeating it. Avoid retrying create/delete or rotating credentials blindly.

## Resource and action map

Command shape: `tomo config <resource> <action> [id] [--set ... | --data ...]`.
Only actions listed for that resource are implemented, even though the top-level
parser shows the union of action names.

| Resource | Actions and important details |
| --- | --- |
| `agents` | `list`, `show`, `create`, `update`, `delete`, `schema`; model, workplace scope, enabled state, system prompt, artifacts |
| `workplaces` | CRUD and `schema`; also `pairing-code`, `enable`, `disable`; generic create defaults to **local** |
| `llm-profiles` | CRUD and `schema`; `default <id>` selects an existing profile; `model_id` on an agent is a profile ID |
| `mcp-servers` | CRUD and `schema`; transports `stdio` and `streamable_http`; saved configuration is not discovery evidence |
| `schedules` | CRUD and `schema`; `pause <id>`, `resume <id>`; assign a real agent ID |
| `knowledge` | CRUD and `schema`; follows the model's user scope; do not infer another user's knowledge from a CLI listing |
| `users` | CRUD and `schema`; create requires username/password; last-enabled-account protection applies |
| `api-keys` | `list`, `show`, `create`, `delete`, `schema`; create requires `user_id`; cannot update an existing key |
| `skills` | `list`, `show`, `update`; fields `enabled`, `name`, `description`, `version`; package install/removal uses `tomo skills` |
| `modules` | `list`, `show`, `update`; fields `enabled`, `name`, `description`, `version`; no generic module `config` payload |
| `settings` | `show`/`list`, `update`; no ID; update existing settings keys |
| `agent-tools` | `show`/`list <agent-id>`, `update <agent-id>` with `{"enabled":{"tool-id":true}}` |
| `agent-skills` | `show`/`list <agent-id>`, `update <agent-id>` with `{"skill_ids":["skill-id"]}` |
| `mcp-items` | `list <server-id>`, `show <item-id>`, `update <item-id>` with `{"enabled":true}` |

CRUD means `list`, `show <id>`, `create`, `update <id>`, `delete <id>`.
Create fields are flags/data; create does not take a positional name/ID in the
generic command. Use `--set name=...` and optional `--set id=...`. Typed resources
expose create/update schemas; `schema` is unavailable for the special resources
without request models. Discover their input shapes from this reference.

## Task playbooks

This file defines command mechanics and resource support. For end-to-end decisions,
load the relevant playbook:

- [Agents and models](agents-models.md): persistent specialists, personas, profile selection, workplace scope.
- [Skills and modules](skills-modules.md): discovery, installations, full assignment saves, reusable procedures.
- [Schedules](schedules.md): complete job prompts, timezone/next-run checks, run history.
- [Channels and settings](channels-settings.md): Telegram, transcription, approval defaults, learning/limits.
- [Memory and knowledge](memory-knowledge.md): retrieval, store selection, corrections, scoped knowledge.
- [Accounts and sessions](accounts-sessions.md): passwords, keys, session identity, artifact delivery.

## Common configuration tasks

### Connect a new machine

On the coordinator:

```bash
tomo workplaces list --json
tomo workplaces create server-x --json
```

The shortcut `workplaces create` takes a positional name, defaults to **tunnel**,
and returns the ID, pairing code, and expiry. The generic equivalent requires
`--set kind=tunnel`. If the record already exists, use `show <id>` or
`pairing-code <id>` rather than creating a duplicate. Codes expire and successful
pairing clears them; use returned expiry/TTL instead of assuming unlimited life.

SSH, install the connector, pair against the coordinator URL reachable from the
target, configure the persistent connector root, and start its service. Load
[connector setup](connector/setup.md) for those commands and account/path rules.
`root_path` on a tunnel record does not configure `TOMO_CONNECTOR_ROOT`.

Creating a CLI workplace does not assign it to any agent or select it in the
current chat. Update the intended agent's actual ID with `workplace_id`, or its
`workplace_scope`/`workplace_ids` when multiple workplaces are required. Preserve
its other assignments. Per-call/session workplace selection is a separate runtime
choice; inspect enabled tools before assuming a switching tool exists.

### Assign an LLM profile

```bash
tomo config llm-profiles schema --json
tomo config llm-profiles create --data @/absolute/profile.json --json
tomo config llm-profiles default <returned-profile-id> --json
tomo config agents update <agent-id> --set model_id=<returned-profile-id> --json
```

Create data can include `name`, `base_url`, `model`, `api_key`, and
`reasoning_efforts`. The agent's `model_id` is the configured profile ID, not the
provider's model string. Omitted fields remain unchanged on update; blank
`api_key` keeps the existing encrypted secret. Reuse a profile for credential
rotation. Subscription/device OAuth onboarding is a separate implemented web
workflow; the configuration CLI does not expose login-start/login-poll.

### Enable tools and skills for an agent

Inspect current selections first:

```bash
tomo config agent-tools show <agent-id> --json
tomo config agent-skills show <agent-id> --json
tomo config agent-tools update <agent-id> --data @/absolute/tool-selection.json --json
tomo config agent-skills update <agent-id> --data @/absolute/skill-selection.json --json
```

Both saves replace the assignment set. Include every item the agent should keep,
not just the addition. Use returned tool IDs and discovered skill IDs; unknown
IDs fail. MCP server/item enablement must also permit an MCP tool. Use the
runtime catalog's actual schemas before calling a tool.

Use `tomo skills sync`, `list`, `install <path> [--id <id>]`, and `uninstall <id>`
for packages. Catalog enablement alone does not install dependencies or change
an external package on disk; bundled skills are restored on discovery.

### Configure channels, memory, and limits

Read `tomo config settings show --json` for actual supported keys and values.
For example:

```bash
tomo config settings update --set telegram_rich_messages=true --json
tomo config settings update --set telegram_allowed_chat_ids='["123456789"]' --json
tomo config settings update --set learning_enabled=true --set concurrency_limit=4 --json
tomo config settings update --set memory_consolidation_cron='0 3 * * *' --json
```

Replace example chat IDs with the intended IDs. Do not clear the Telegram
allowlist to work around access failures. Bot/transcription tokens are secrets;
public reads mask them, and blank secret fields preserve the stored value.
Unknown settings fail. Use `llm-profiles` for model configuration rather than
confusing legacy `llm_*` settings with profile selection.

## Apply and verify runtime behavior

CLI writes persist immediately but do not refresh another process's runtime
objects. For active MCP sessions, connector sockets, schedule jobs, Telegram
connections, or memory consolidation jobs, apply the change through the actual
service/container lifecycle when needed. For a managed systemd user install,
`tomo service restart` controls the coordinator. Do not use it for Docker or a
foreground source process; inspect that deployment's lifecycle instead.

Restart is not a default step for every read, agent-field update, or new tunnel
pairing. Complete the requested configuration first, apply runtime changes only
where relevant, and verify the affected functionality after reload. Deleting or
disabling a tunnel through the CLI alone does not close the running server's
existing socket; use its lifecycle when immediate disconnection is required.

Tunnel CLI JSON has `online: null` and `status_source: "database"`. `connected`
is a last-recorded status, not an observation of the active WebSocket. Use the
available `list_workplaces` runtime tool and a harmless remote probe for live
verification. MCP item listings are cached discovery data; CLI saves do not
perform a live refresh or prove tool availability. Use the supported runtime
or authenticated web flow for discovery, chat/session operations, approvals,
OAuth, and execution; do not invent CLI counterparts.

If a command is absent, identify the installed version/checkout with `--help`
and [install paths](install.md). Do not silently edit raw SQLite, install a new
Tomo instance, or add credentials to compensate for running in the wrong place.
Report the actual failure and the smallest remaining prerequisite.
