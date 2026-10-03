# Local configuration CLI

Run `tomo` on the coordinator under the OS account running Tomo, with the same
`TOMO_HOME`/`TOMO_DB_PATH`. These commands use the existing database and model
functions directly. They do not make HTTP requests to Tomo, require an API key,
start the server, or prompt for input. They refuse to create a missing database.

For a new connector:

```bash
tomo workplaces create server-x
tomo workplaces list
tomo workplaces show tun_server_x --json
tomo workplaces pairing-code tun_server_x --json
```

Use the actual ID printed by `create`/`list`. Creation defaults to `tunnel` and
prints its short-lived pairing code. SSH, connector installation, and
`tomo-connector pair --code ... --server ...` remain normal terminal commands.
The connector's filesystem root is configured on the target with
`TOMO_CONNECTOR_ROOT`, independently of the workplace record.

Other configuration uses a consistent command shape:

```bash
tomo config agents list
tomo config agents create --set name=builder --set workplace_id=tun_server_x
tomo config agents update builder --set enabled=false
tomo config llm-profiles default my_profile
tomo config schedules pause nightly
tomo config settings update --set telegram_rich_messages=true
```

| Resource | Actions |
| --- | --- |
| `agents`, `workplaces`, `llm-profiles`, `mcp-servers`, `schedules`, `knowledge`, `users` | `list`, `show ID`, `create`, `update ID`, `delete ID`, `schema` |
| `api-keys` | `list`, `show ID`, `create`, `delete ID`, `schema` |
| `skills` | `list`, `show ID`, `update ID` |
| `settings` | `show`, `update` |
| `agent-tools`, `agent-skills` | `show AGENT_ID`, `update AGENT_ID` |
| `mcp-items` | `list SERVER_ID`, `show ITEM_ID`, `update ITEM_ID` |
| `llm-profiles` | Also `default ID` |
| `schedules` | Also `pause ID`, `resume ID` |
| `workplaces` | Also `pairing-code ID`, `enable ID`, `disable ID` |

`--set FIELD=VALUE` is repeatable. Values parse as JSON when valid, otherwise as
strings; use JSON booleans (`true`/`false`) and arrays for typed fields. Field names
accept underscores or hyphens. `schema` exposes the same request schemas used by
the web interface so agents can discover fields and required values.

For structured data or secrets, pass `--data @/path/to/file.json`, `--data -`
(read stdin), or a literal JSON object. `--set` overrides fields from `--data`.
Unknown fields and invalid values fail with exit code 1 without echoing secret
inputs in validation errors. Read output uses the existing public/masked views;
creating an account API key intentionally prints its token once.

```bash
tomo config llm-profiles create --data @profile.json --json
tomo config agent-tools update builder --data '{"enabled":{"bash":true,"read_file":true}}'
tomo config agent-skills update builder --data '{"skill_ids":["tomo"]}'
tomo config mcp-items update item_id --set enabled=false
```

Tool and skill assignments replace the selected set, matching the web UI's Save
operation. Use `show` first and send the complete intended selection. Existing
`tomo skills list|sync|install|uninstall` commands manage filesystem packages;
`config skills update ID --set enabled=false` manages their catalog settings.

Configuration writes are immediately persisted. Runtime objects belong to the
running server process: restart with `tomo service restart` to apply changes to
active MCP connections, connector sockets, scheduler jobs, Telegram connections,
or memory consolidation jobs. A CLI write does not itself refresh those objects.
Tunnel `status` is the last database value, while JSON `online` is `null` and
`status_source` is `database`; use the running server to verify live connectivity.
These are configuration commands; device OAuth authorization, chat execution,
MCP discovery, and other runtime operations are separate workflows.

Live plugins use `tomo plugins list/install/enable/disable/reload/uninstall`,
which contact the running server. See [Live plugins](plugins.md).
