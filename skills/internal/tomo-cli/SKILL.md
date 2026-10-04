---
name: tomo-cli
description: "Use Tomo CLI for runtime logs, diagnosis, configuration and service control."
version: 1.0
---

# Tomo CLI

Use when asked to inspect live logs, diagnose runtime/plugin failures, or operate
Tomo through its CLI. The executable is `tomo`; in a source checkout use
`python -m cli` with the project's environment. Start with `tomo --help` or the
specific subcommand's `--help` rather than inventing commands.

## Choose the correct host

Run on the **coordinator**, under its OS account and actual `TOMO_HOME` /
`TOMO_DB_PATH`. A workplace shell can be on a different machine. For Docker,
run inside the coordinator container with its mounted data roots. Do not change
roots just to make a command work; that can inspect or modify another install.
Local configuration needs no API key. `tomo` does not start the server.

## Diagnose from retained evidence

Use bounded commands first; an unbounded `-f` can hang an agent shell tool.

```bash
tomo logs --level WARNING -n 100 --json
tomo logs --type plugin -n 100 --json
tomo logs --session SESSION_ID -n 200 --json
tomo logs --request REQUEST_ID --json
tomo logs --type llm --event failed --json
```

For a human terminal, use `tomo logs -f`, optionally with the same filters.
For a bounded capture on hosts with GNU timeout:

```bash
timeout 15s tomo logs -f --type tool --json
```

Timeout exit 124 means the capture window ended, not that Tomo failed.
Levels are minimum severity: DEBUG, INFO, WARNING, ERROR, CRITICAL. Types include
http, chat, agent, llm, tool, scheduler, telegram, session_title, mcp, plugin and
system. `--tail` counts matching records across current and retained files.
`--event` filters structured lifecycle records; older/plain module messages may
have no event. Don't assume no matching record proves an operation never ran:
check active level, filters, roots, retention and whether the server was restarted
after enabling logging.

Correlate session/request IDs across subsystems and inspect operation IDs,
duration, model/provider and traceback. Distinguish started-but-not-finished,
failed, cancelled, closed and normally completed boundaries. A completed stream
is not proof the model's answer was correct. Plugin host errors are captured,
but arbitrary plugin `print()`/custom logger output isn't automatically captured.

The web alternative is **System → Logs** (`/system#logs`), administrator-only,
with live SSE, filters, pause/resume and expandable record details.
Logs live at `$TOMO_HOME/logs/tomo.jsonl` unless `TOMO_LOG_DIR` is set. Rotation
defaults to 10 MiB and five backups. `TOMO_LOG_LEVEL`, `TOMO_LOG_MAX_BYTES`,
`TOMO_LOG_BACKUP_COUNT` configure logging; restart the server after changes.
Use one server writer per log directory. Logs can contain sensitive exception
or existing module messages: summarize evidence, redact secrets and do not
paste raw logs into external services without permission.

## Other operations

- `tomo service status|start|stop|restart`: managed systemd user service only.
  Don't restart a service merely to read its logs.
- `tomo skills list|sync|install|uninstall`: discover or manage skill packages;
  installs don't automatically grant tools or assign the skill to every agent.
- `tomo config <resource> schema --json`: discover local configuration fields.
  Read before updating; preserve unrelated fields and full assignment selections.
- `tomo plugins --help`, `tomo workplaces --help`, `tomo http --help`,
  `tomo connection --help`, `tomo secret --help`: inspect actual supported actions.
- Update/uninstall are state-changing, not diagnostic commands. Use only when
  requested; `--purge` deletes data.

For detailed syntax and runtime reload rules, load the bundled `tomo` skill:
`use_skill(skill_id="tomo", file="references/cli.md")` or
`use_skill(skill_id="tomo", file="references/configuration-cli.md")`.
For private credentials, use the `secret-store` skill; don't expose secret values
through CLI stdout or conversation history.

Finish with the host/root, observed failure and correlation ID, action taken,
and verification. If logging wasn't enabled or evidence was rotated away, say
so rather than claiming the subsystem is healthy.
