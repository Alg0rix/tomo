# Config

Two roots. There is no `TOMO_WORKDIR`.

| Root | Default | Holds |
|------|---------|--------|
| `$TOMO_HOME` | `~/.tomo` | Config, secrets, SQLite, personas, library. Not the agent cwd. |
| `$TOMO_WORK` | `~/tomo` | Tool cwd `$TOMO_WORK/<agent_id>`, unless a local workplace is bound. |

Process environment wins over `$TOMO_HOME/.env`. `TOMO_HOME` itself cannot be set inside that file, because the file is found from `TOMO_HOME`. `tomo.yaml` is non-secret preferences only (`brand`, `theme`). Never put API keys or the master key there.

For editable settings, profiles, accounts, and agent/workplace configuration,
prefer the local CLI on the coordinator. Load [CLI configuration](configuration-cli.md)
for resource schemas and commands. Environment/root changes still belong to the
running deployment's config; a database setting does not override its environment.

## Which knob

| Need | Where |
|------|--------|
| Session cookie and bootstrap admin password | `$TOMO_HOME/.env`: `TOMO_SESSION_SECRET`, `TOMO_ADMIN_PASSWORD`. The password only seeds an empty users table. Change it later with `tomo config users update <id>` or System → Accounts. |
| Encrypt UI secrets | `TOMO_SECRET_KEY`, else `$TOMO_HOME/.secret_key` (mode `0600`, never overwritten). Losing it makes encrypted SQLite settings unrecoverable. |
| LLM base URL, model id, API key | `tomo config llm-profiles` or System → Models. The key is encrypted in SQLite. A blank save keeps the existing key. GET settings returns masked values. |
| Persona | `$TOMO_HOME/SOUL.md`, plus `agents/<id>/SYSTEM.md` and `SOUL.md`. |
| Curated notes | `memories/users/<user_id>/USER.md`. Per-login agent notes: `agents/<id>/users/<user_id>/MEMORY.md`. Legacy shared files `memories/USER.md` and `agents/<id>/MEMORY.md` are the web fallback. |
| Markdown vault | `$TOMO_HOME/memory/vault/<user_id>/`, when System → General has the vault on. |
| Artifacts | `$TOMO_HOME/sessions/<session_id>/artifacts/`. |
| Database | `$TOMO_HOME/state/tomo.db`. `TOMO_VAR_DIR` changes the directory; `TOMO_DB_PATH` changes the file. There is no automatic migration from a legacy `var/tomo.db`. |
| Bind | `TOMO_HOST` default `127.0.0.1`, `TOMO_PORT` default `8787`, `TOMO_RELOAD` default off. |
| Skills from outside the repo | `TOMO_SKILLS_EXTERNAL_DIRS`, colon-separated. Empty disables external discovery. Unset scans `~/.agents/skills`, `~/.agent/skills`, `~/.tomo/skills`, and `~/.claude/skills`. |

## Home tree

```text
$TOMO_HOME/
├── tomo.yaml
├── .env
├── .secret_key
├── SOUL.md
├── memories/users/<user_id>/USER.md
├── memories/USER.md
├── library/{skills,memory}
├── agents/<id>/{SYSTEM.md,SOUL.md,knowledge}
├── agents/<id>/users/<user_id>/MEMORY.md
├── agents/<id>/MEMORY.md
├── memory/vault/<user_id>/
├── sessions/<session_id>/artifacts/
├── workplaces/
└── state/tomo.db
```

Allowed familiar names: `SOUL.md`, `SYSTEM.md`, `MEMORY.md`, `USER.md`, `.env`, `.secret_key`. Do not create `secrets.env`, `identity.md`, or `prompt.md`. First start copies `SOUL.md` and `tomo.yaml` from the shipped `defaults/`. It does not bind-mount the git tree as live config.

## Server safety

Non-loopback bind refuses the dev defaults `TOMO_SESSION_SECRET=tomo-dev-secret-change-me` and `TOMO_ADMIN_PASSWORD=tomo`. `TOMO_SECRET_KEY` is the at-rest master key, not the session secret.

`TOMO_TRUST_PROXY=1` only behind a reverse proxy that strips client-spoofed `X-Forwarded-For`. `TOMO_COOKIE_SECURE=1` forces HTTPS-only cookies; it turns on by itself when the bind host is not loopback. `TOMO_FS_BROWSE_ROOT` jails admin filesystem browse (default: the operator's home). `TOMO_EVAL_UI=1` shows the deferred eval UI. `TOMO_IN_CONTAINER=1` marks the process as a container so Settings → Instinct hides self-update.

Docker sets `TOMO_HOME=/data/home` and `TOMO_WORK=/data/work`. See `references/install.md` for which tree that is.
