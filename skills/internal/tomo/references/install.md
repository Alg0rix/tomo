# Install

Three ways to run the coordinator. Config and work directories are separate from the code tree. Details of env vars are in `references/config.md`. Connector pairing is in `references/connector.md`.

## Path map

| What | Script install (Linux, systemd user) | Docker Compose | Source checkout |
|------|--------------------------------------|----------------|-----------------|
| Code | `~/.local/share/tomo/app` (git + `.venv`) | `/app` in `ghcr.io/alg0rix/tomo` | The clone |
| CLI | `~/.local/bin/tomo` | `python -m cli` inside the running container | `uv run python -m cli` or `uv run tomo` after `uv sync` |
| Unit | `~/.config/systemd/user/tomo.service` | `restart: unless-stopped` | none |
| `$TOMO_HOME` | `~/.tomo` | volume `tomo-home` → `/data/home` | `~/.tomo` unless exported |
| `$TOMO_WORK` | `~/tomo` | volume `tomo-work` → `/data/work` | `~/tomo` unless exported |
| UI | `http://127.0.0.1:8787` | host port `TOMO_PUBLISH_PORT` (default `8787`) | `http://127.0.0.1:8787` |
| This skill | `<install>/skills/internal/tomo` | `/app/skills/internal/tomo` | `<clone>/skills/internal/tomo` |

The unit sets `TOMO_HOME=%h/.tomo`, `TOMO_WORK=%h/tomo`, `WorkingDirectory=%h/.local/share/tomo/app`, and `ExecStart` to that tree's `.venv/bin/python -m app.main`. It also loads `EnvironmentFile=-%h/.tomo/.env`.

Do not edit the managed tree for day-to-day development. `tomo update` only updates `~/.local/share/tomo/app`.

## Script install

Requires `git`. Installs `uv` into `~/.local/bin` when it is missing.

```bash
curl -fsSL https://raw.githubusercontent.com/Alg0rix/tomo/main/scripts/install.sh | bash
# from a checkout: bash scripts/install.sh
# --no-start    write and enable the unit, do not start it
# --branch NAME track a branch (default main)
```

Overrides: `TOMO_REPO_URL`, `TOMO_INSTALL_DIR`, `TOMO_BIN_LINK`. Headless hosts need `loginctl enable-linger "$USER"` so the user unit survives logout. Logs: `journalctl --user -u tomo -f`.

Settings → Instinct → Update (`GET`/`POST /api/update`) exists only when the running tree is that managed git install. It spawns `python -m cli update -y`. Containers (including `TOMO_IN_CONTAINER=1`, `/.dockerenv`, or a container cgroup) and ordinary dev clones hide it.

## Docker Compose

```bash
cp .env.example .env   # TOMO_SESSION_SECRET and TOMO_ADMIN_PASSWORD
docker compose up -d   # or: docker compose up -d --build
```

The image listens on `8787` and publishes `${TOMO_PUBLISH_PORT:-8787}`. Bind-mounting host directories instead of the named volumes needs those directories owned by uid `10001` (the `tomo` user in the image). `docker compose down` keeps the volumes. `docker compose down -v` deletes Home and Work. The image does not include the Go connector.

## Source checkout

```bash
git clone https://github.com/Alg0rix/tomo.git
cd tomo
uv sync
uv run python -m app.main
```

## Connector binary

Separate from the coordinator. On the target machine:

```bash
curl -fsSL https://raw.githubusercontent.com/Alg0rix/tomo/main/scripts/install-connector.sh | bash
```

| What | User install | Root install |
|------|--------------|--------------|
| Binary | `~/.local/bin/tomo-connector` | `/usr/local/bin/tomo-connector` for the system unit. The curl installer still defaults to `~/.local/bin`; set `TOMO_CONNECTOR_BIN_DIR=/usr/local/bin` when updating a root service. |
| State | `~/.tomo-connector` or `$TOMO_CONNECTOR_HOME` | same, for the account that paired |
| File root | `$TOMO_CONNECTOR_ROOT` or `<state>/work` | same |
| Unit | `~/.config/systemd/user/tomo-connector.service` | `/etc/systemd/system/tomo-connector.service` |

Pin a release with `TOMO_CONNECTOR_VERSION=<tag>`. Pairing, service, and MCP steps are `references/connector.md`.
