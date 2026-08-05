# Deployments

How to run Tomo in production-ish environments. For local development from a
git clone, see the [README](../README.md#develop-from-source).

| Method | Best for |
|--------|----------|
| [systemd user install](#systemd-user-linux) | Single Linux host / VPS you SSH into |
| [Docker Compose](#docker-compose) | Containers, homelab, hosts without systemd user units |

Both keep durable state under **`$TOMO_HOME`** (config, SQLite, secrets) and
**`$TOMO_WORK`** (agent tool workspaces). Do not bind-mount the git repo as live
config — Tomo seeds Home from shipped `defaults/` on first start.

---

## systemd user (Linux)

Recommended bare-metal / VM install:

```bash
curl -fsSL https://raw.githubusercontent.com/Alg0rix/tomo/main/scripts/install.sh | bash
```

UI: [http://127.0.0.1:8787](http://127.0.0.1:8787). Details, update, and
uninstall: [README → Install](../README.md#install-linux-systemd-user).

Headless hosts: `loginctl enable-linger $USER` so the unit survives logout.

---

## Docker Compose

Repo root ships `Dockerfile`, `docker-compose.yml`, and `.env.example`.

### 1. Secrets

```bash
git clone https://github.com/Alg0rix/tomo.git
cd tomo
cp .env.example .env
# edit .env — set TOMO_SESSION_SECRET and TOMO_ADMIN_PASSWORD
openssl rand -hex 32   # paste into TOMO_SESSION_SECRET
```

Tomo refuses to bind `0.0.0.0` while those still use the insecure defaults
(`tomo-dev-secret-change-me` / `tomo`).

### 2. Start

```bash
docker compose up -d --build
```

Open [http://127.0.0.1:8787](http://127.0.0.1:8787) (or
`http://<host>:$TOMO_PUBLISH_PORT`). Log in with `admin` and the password from
`.env`, then change it under System → Accounts.

```bash
docker compose logs -f tomo
docker compose down          # stop; volumes keep data
docker compose down -v       # also delete Home/Work volumes — destructive
```

### 3. What is persisted

| Volume | Inside container | Purpose |
|--------|------------------|---------|
| `tomo-home` | `/data/home` (`$TOMO_HOME`) | SQLite, `.secret_key`, SOUL, agents, library |
| `tomo-work` | `/data/work` (`$TOMO_WORK`) | Per-agent tool cwd |

Backup = copy those volumes (or bind-mount host directories instead of named
volumes — see below). Losing `.secret_key` / `TOMO_SECRET_KEY` makes encrypted
settings in SQLite unrecoverable.

### 4. Environment reference

Set in `.env` (or Compose `environment:`). Process env wins over
`$TOMO_HOME/.env`.

| Variable | Role |
|----------|------|
| `TOMO_SESSION_SECRET` | Session cookie signing (**required** off loopback) |
| `TOMO_ADMIN_PASSWORD` | Bootstrap `admin` password (**required** off loopback) |
| `TOMO_SECRET_KEY` | Optional Fernet master key; else file `$TOMO_HOME/.secret_key` |
| `TOMO_HOST` / `TOMO_PORT` | Bind address (Compose sets `0.0.0.0:8787`) |
| `TOMO_PUBLISH_PORT` | Host port mapped to the container (default `8787`) |
| `TOMO_TRUST_PROXY` | `1` when behind a reverse proxy that strips client `X-Forwarded-For` |
| `TOMO_COOKIE_SECURE` | `1` for HTTPS-only cookies (auto-on when not binding loopback) |
| `TOMO_HOME` / `TOMO_WORK` | Override paths (Compose uses `/data/home` and `/data/work`) |

LLM API keys and model profiles are configured in the UI (System → Models) and
stored encrypted in SQLite — not in Compose env.

### 5. Bind-mount host directories (optional)

Replace named volumes when you want the data on a known host path:

```yaml
# docker-compose.override.yml (gitignored pattern) or edit docker-compose.yml
services:
  tomo:
    volumes:
      - ./data/home:/data/home
      - ./data/work:/data/work
```

```bash
mkdir -p data/home data/work
# uid 10001 is the `tomo` user inside the image
sudo chown -R 10001:10001 data
docker compose up -d --build
```

### 6. Reverse proxy

Put TLS termination in front (Caddy / nginx / Traefik). Example Caddy:

```caddy
tomo.example.com {
  reverse_proxy 127.0.0.1:8787
}
```

Then in `.env`:

```bash
TOMO_TRUST_PROXY=1
TOMO_COOKIE_SECURE=1
```

WebSocket tunnels for **tomo-connector** and SSE chat need proxy buffering
disabled / long timeouts — use your proxy’s streaming WebSocket settings.

### 7. Connector / workplaces from Docker

- **Tunnel workplaces:** pair `tomo-connector` on remote machines against the
  public URL of this Tomo instance (`https://tomo.example.com`), not
  `localhost` inside the container.
- **Local path workplaces** see the **container filesystem** (under
  `$TOMO_WORK`), not the Docker host’s `/home`. Prefer tunnel or SSH
  workplaces to reach host or other machines.
- **SSH workplaces** work if the container can reach the SSH target (network
  + keys/password stored in Tomo).

### 8. Update

```bash
git pull
docker compose up -d --build
```

Named volumes keep Home/Work across rebuilds. Pin a release checkout with
`git checkout v0.2.0` before build if you want a fixed tag.

### 9. Limitations

- Image runs the **coordinator** (FastAPI UI + API). The Go `tomo-connector`
  binary is separate — install on each remote device via
  [install-connector.sh](../README.md#install-connector-tunnel-workplaces).
- No built-in multi-replica / shared SQLite story — run **one** Tomo container
  per data directory.
- Agent “local Docker isolation” on the roadmap is not this Compose file;
  tools still run as processes inside the Tomo container (or on workplaces).

---

## Related

- [Architecture notes](architecture.md)
- [Machine connectivity (README)](../README.md#machine-connectivity)
- [Tomo Home layout (README)](../README.md#configuration)
