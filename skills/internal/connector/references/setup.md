# Installation and lifecycle

## Prepare the target

Choose an OS account with the access the task needs. Pair and run the service as
the same account with the same state directory. Confirm the coordinator's base
HTTP(S) URL is reachable from the target; use HTTPS for connections carrying
credentials across untrusted networks. The connector appends its API paths.

## Install a binary

The repository installer supports Linux/macOS on amd64/arm64. On the target:

```bash
curl -fsSL https://raw.githubusercontent.com/Alg0rix/tomo/main/scripts/install-connector.sh -o /tmp/install-tomo-connector.sh
bash /tmp/install-tomo-connector.sh
export PATH="$HOME/.local/bin:$PATH"
tomo-connector help
```

Inspect the downloaded script if needed before execution. Set
`TOMO_CONNECTOR_VERSION` to a real release tag to pin it; the default is `latest`.
The installer also accepts `TOMO_CONNECTOR_REPO` and `TOMO_CONNECTOR_BIN_DIR`.
Do not assume an example version exists. Installer assets are named
`tomo-connector-<linux|darwin>-<amd64|arm64>`.

For a source checkout, run in `connector/`:

```bash
go build -o tomo-connector ./cmd/tomo-connector
./tomo-connector help
```

`make dist` defines Linux/macOS amd64/arm64 and Windows amd64 release targets.
For Windows, inspect available release assets and runtime requirements first.
The curl installer and systemd instructions do not apply; shell execution still
requires `bash`, and Python execution requires `python3` on the target.

## Register and pair

Create a tunnel workplace in **Workplaces**, then use its pairing code. The
available agent tool can create a record with:

```text
register_workplace(name="build-host", kind="tunnel", assign_to_agent=true, use_now=true)
```

This tool's result identifies the workplace but does not return the pairing
code; retrieve it through the authenticated workplace UI/admin API. It does not
install or connect the target, and `root_path` on this tunnel tool call does not
configure the connector's filesystem root. Avoid creating duplicate tunnel
records during retries.

Codes expire after 15 minutes and are cleared on successful pairing. Generate
a fresh code on the same record if needed. On the target, substitute the actual
code and coordinator base URL:

```bash
tomo-connector pair --code <CODE> --server https://<coordinator-host>
tomo-connector status
tomo-connector run
```

`run` is a foreground process. Use a separate terminal/session or a service to
keep it alive. `TOMO_CONNECTOR_PAIR_AND_RUN=1` makes `pair` enter `run` immediately.
`status` reads local credentials and prints a masked token; it is not a live
connectivity test. Do not print `state.json` to diagnose pairing.

## State and root

| Setting | Purpose | Default |
|---------|---------|---------|
| `TOMO_CONNECTOR_HOME` | Pairing state directory | `~/.tomo-connector` |
| `TOMO_CONNECTOR_ROOT` | File-tool root and default execution directory | `<connector-home>/work` |
| `TOMO_CONNECTOR_BIN` | Binary destination for `service install` | User: `~/.local/bin/tomo-connector`; root: `/usr/local/bin/tomo-connector` |

Pairing writes `state.json` containing `server_url`, `workplace_id`, and `token`
with mode 0600; a newly created state directory uses 0700. Protect this file.
To use a project root in foreground mode:

```bash
mkdir -p /absolute/project/path
TOMO_CONNECTOR_ROOT=/absolute/project/path tomo-connector run
```

Choose the actual authorized directory; do not create a substitute for a missing
project without checking the intended location. The root is not a container or
chroot and does not restrict OS-level shell access.

## Linux systemd

Pair first, then run as the paired account:

```bash
tomo-connector service install
tomo-connector service status
```

Non-root installs a user unit under `~/.config/systemd/user/`; root installs a
system unit under `/etc/systemd/system/`. Root's service runs with root privileges.
`service install --no-start` enables the unit without starting it. Supported
actions are `start`, `stop`, `restart`, `status`, `enable`, `disable`, and
`uninstall`. Enable/disable alone do not start/stop a running service.

For custom roots/state, inspect the generated unit and add a persistent drop-in:

```bash
systemctl --user edit tomo-connector.service
```

```ini
[Service]
Environment="TOMO_CONNECTOR_ROOT=/absolute/project/path"
Environment="TOMO_CONNECTOR_HOME=/absolute/connector-state"
```

```bash
systemctl --user daemon-reload
systemctl --user restart tomo-connector.service
systemctl --user cat tomo-connector.service
```

For root's system service omit `--user`. Include the HOME override only when
using custom state and pair with that same environment. The default user-unit
template hardcodes `%h/.tomo-connector`; exporting custom HOME/ROOT in an
interactive shell does not guarantee they reach the service. An optional
`loginctl enable-linger "$USER"` keeps a user service available after logout,
subject to OS permissions. Non-systemd hosts need their own process supervisor.

## SSH-assisted installation

Workplaces offers installation via SSH. Supply the real SSH account, a
coordinator URL reachable from the remote host, and available architecture/release.
The target downloads the binary, installs the service, pairs, and becomes a
tunnel workplace. SSH host keys must already be trusted by the coordinator;
the SSH backend rejects unknown keys. Verify the fingerprint through a trusted
channel instead of disabling host-key checks.

Use the returned API result and live workplace status. The implemented admin
endpoint returns the installation result directly; do not assume an asynchronous
job/polling endpoint exists just because a UI or old design mentions one.

## Update, disconnect, and remove

Re-running the download installer replaces its destination atomically and
restarts an enabled/active service. Match the service's **actual** binary path:
the installer's default is `~/.local/bin` even for root, while root's service
default is `/usr/local/bin`. Set `TOMO_CONNECTOR_BIN_DIR=/usr/local/bin` when
updating that root installation, or set the appropriate custom directory.
For source-built binaries, rebuild and install into the unit's actual ExecStart
path, then restart. Verify the connector version reported by Tomo and a remote
probe. Keep the previous binary/release information when rollback is needed.

Stop the running service/process before `logout` or re-pairing. `run` loads
credentials at startup, so deleting/changing local state does not invalidate an
already running connection. Restart after pairing changes.

- `service uninstall` stops/disables and removes the unit, keeping binary/state.
- `logout` removes local `state.json`; it does not revoke the server token.
- Disable a workplace in Tomo to disconnect it and reject new authentication;
  re-enabling retains its token and allows reconnection.
- Deleting the workplace removes the server record and drops its live session.
  Treat deletion as a separate requested action, not a routine reconnect repair.

For credential replacement, use a fresh pairing code and complete pairing on
the intended machine, then verify connectivity. Generating a code alone does
not revoke an existing connector token. Remove binaries/state directories only
when requested; the work root can contain user files.
