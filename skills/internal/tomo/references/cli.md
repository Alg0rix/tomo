# CLI

`tomo` is the console script (`python -m cli`). It does not start the server. The server is `python -m app.main`, which is what the systemd unit and the Docker image run.

The binary on a script install is the symlink `~/.local/bin/tomo` into `~/.local/share/tomo/app`. `tomo update` always targets that managed checkout. It does not update a dev clone or a container.

| Command | Effect |
|---------|--------|
| `tomo logs [-f] [-n N] [--level LEVEL] [--type TYPE] [--session ID] [--request ID] [--event EVENT] [--json]` | Inspect retained logs or follow across rotation. For diagnosis, load the `tomo-cli` skill. UI: System → Logs (admin only). |
| `tomo update [-y]` | Stash a dirty tree, fetch, fast-forward the tracked branch, hard-reset to `origin/<branch>` when fast-forward fails, `uv sync`, restart the user service. |
| `tomo service status\|start\|stop\|restart` | `systemctl --user` on `tomo.service`. |
| `tomo uninstall [-y] [--purge]` | Stop and disable the unit, remove the unit file and the managed-install symlink, delete `~/.local/share/tomo/app`. `--purge` also deletes `TOMO_HOME` and `TOMO_WORK` when both are under the user home. |
| `tomo skills list` | Sync, then print id, on/off, source, name. |
| `tomo skills sync` | Rescan bundled, library, and external roots into the catalog. |
| `tomo skills install <path> [--id <id>]` | Copy a skill directory (or a `SKILL.md`'s parent) into `$TOMO_HOME/library/skills`. |
| `tomo skills uninstall <id>` | Delete a library copy. External packages stay on disk; remove the directory, then sync. |

`tomo update` exits with an error when `~/.local/share/tomo/app` is not a git checkout, and tells you to run `scripts/install.sh`. `-y` skips the confirmation prompts, including stash restore. If `uv` is missing from `PATH`, update looks at `~/.local/bin/uv`. A failed service restart is a warning: the git sync may already have landed. A newly generated bootstrap admin password is printed once.

`tomo uninstall` without `--purge` keeps `~/.tomo` and `~/tomo`. `--purge` refuses a data path outside the user home. Without `-y` it asks before deleting data; declining aborts the purge after the service and code are already gone.

`tomo skills uninstall` does not delete this bundled package. Discovery scans `<repo>/skills/internal` first, and that copy keeps its id: a library or `~/.agents/skills` package with the same id does not replace it. The next sync puts a removed catalog row for an internal skill back.

Paths and install methods are `references/install.md`. Env and secrets are `references/config.md`.

## Local configuration

Load [CLI configuration](configuration-cli.md) for the full resource/action map,
JSON input, schemas, assignment semantics, secret handling, and runtime reloads.
The short path to create a tunnel on the coordinator is:

```bash
tomo workplaces create server-x --json
```

The shortcut defaults to `tunnel`; generic `tomo config workplaces create`
defaults to `local` unless `--set kind=tunnel` is supplied. Use returned IDs.
Configuration runs locally with the coordinator's existing OS account/data roots;
SSH and connector installation remain ordinary terminal commands on the target.
CLI persisted status does not prove the target is online.
