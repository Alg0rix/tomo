"""Install the Tomo Connector on a remote host over SSH.

Fetches a prebuilt ``tomo-connector-{os}-{arch}`` binary from GitHub Releases
(repo ``Alg0rix/tomo``), installs it into ``~/.local/bin`` (or
``/usr/local/bin`` when the remote user is root), registers a systemd unit
(``--user`` for normal users, system unit for root), pairs it against this
coordinator, and registers the host as a ``tunnel`` workplace.

Design notes
------------
* SSH comes from :mod:`app.workplaces.ssh_exec` — Paramiko with
  ``RejectPolicy`` (never auto-add host keys).
* Remote commands run non-interactively and with a strict timeout; outputs are
  capped so a chatty host cannot fill memory.
* No binary bytes ever travel through the API — the remote fetches the asset
  itself with ``curl -fsSL`` (mirroring ``scripts/install-connector.sh``).
"""

from __future__ import annotations

import re
import shlex
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from app.workplaces import ssh_exec

# Asset name + version scheme used by the connector CI (scripts/install-connector.sh).
RELEASE_REPO = "Alg0rix/tomo"
LATEST_URL = f"https://github.com/{RELEASE_REPO}/releases/latest/download/{{asset}}"
TAG_URL = f"https://github.com/{RELEASE_REPO}/releases/download/{{version}}/{{asset}}"
SHA256SUMS_TAG_URL = (
    f"https://github.com/{RELEASE_REPO}/releases/download/{{version}}/SHA256SUMS"
)

# Mirrors the asset naming of the connector release pipeline.
_GOOS = {
    "linux": "linux",
    "darwin": "darwin",
    "freebsd": "freebsd",
    "openbsd": "openbsd",
    "netbsd": "netbsd",
}
_GOARCH = {
    "x86_64": "amd64",
    "amd64": "amd64",
    "i386": "386",
    "i686": "386",
    "aarch64": "arm64",
    "arm64": "arm64",
    "armv7l": "arm",
    "armv6l": "arm",
}

_UNIT_NAME = "tomo-connector.service"
_BIN_SUBDIR = ".local/bin"
_BIN_NAME = "tomo-connector"

# How long we wait for the connector to come online right after pairing.
_POLL_TIMEOUT = 30.0
_POLL_INTERVAL = 1.0

MAX_LOG_BYTES = 16 * 1024  # keep returned log tidy
_OUTPUT_CAP = 64 * 1024  # cap per remote command (same as ssh_exec)

_SUPPORTED_SYSTEMD = ("linux",)


def _cap(text: str) -> str:
    if len(text) <= _OUTPUT_CAP:
        return text
    return text[:_OUTPUT_CAP] + "\n[truncated]"


class InstallError(Exception):
    """Installation step failed. ``retryable`` hints whether re-running helps."""

    def __init__(self, stage: str, message: str, *, retryable: bool = True) -> None:
        super().__init__(f"{stage}: {message}")
        self.stage = stage
        self.retryable = retryable


def _now() -> float:
    return time.time()


@dataclass
class Result:
    """Outcome of an SSH install + pair + connect round."""

    workplace: dict[str, Any]
    status: str  # connected | pairing | installed | failed
    log: list[str] = field(default_factory=list)
    exit_code: int = 0

    def append(self, line: str) -> None:
        self.log.append(_cap(line))

    @property
    def log_text(self) -> str:
        return "\n".join(self.log)


def _remote_uname(host: str, port: int, user: str, password: str, key: str) -> str:
    """Detect remote architecture; returns e.g. ``linux amd64``."""
    client = ssh_exec.connect(
        {
            "ssh_host": host,
            "ssh_port": port,
            "ssh_user": user,
            "ssh_password": password,
            "ssh_key": key,
        }
    )
    try:
        _stdin, stdout, stderr = client.exec_command(
            "uname -s; uname -m", timeout=30
        )
        out = stdout.read().decode("utf-8", errors="replace")
        err = stderr.read().decode("utf-8", errors="replace")
        code = stdout.channel.recv_exit_status()
    finally:
        client.close()
    if code != 0:
        raise InstallError(
            "detect", f"uname failed (exit {code}): {_cap(err or out)}", retryable=True
        )
    lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
    if len(lines) != 2:
        raise InstallError(
            "detect",
            f"unexpected uname output: {_cap(out)}",
            retryable=True,
        )
    _os, _arch = lines[0], lines[1]
    return _normalize_os_arch(_os, _arch)


def _normalize_os_arch(os_name: str, arch: str) -> str:
    os_name = (os_name or "").strip().lower()
    arch = (arch or "").strip().lower()
    goos = _GOOS.get(os_name)
    if goos is None:
        raise InstallError(
            "detect",
            f"unsupported remote OS: {os_name!r} (expected linux/darwin/freebsd/…)",
            retryable=False,
        )
    goarch = _GOARCH.get(arch)
    if goarch is None:
        raise InstallError(
            "detect",
            f"unsupported remote arch: {arch!r}",
            retryable=False,
        )
    return f"{goos} {goarch}"


def _run_remote(
    client: Any,
    script: str,
    *,
    timeout: float = 60.0,
    label: str = "cmd",
) -> tuple[int, str, str]:
    """Run a bash script non-interactively; returns (exit_code, stdout, stderr)."""
    wrapped = f"set -euo pipefail\n{script}"
    _stdin, stdout, stderr = client.exec_command(wrapped, timeout=timeout)
    _stdin.close()
    out = stdout.read().decode("utf-8", errors="replace")
    err = stderr.read().decode("utf-8", errors="replace")
    code = stdout.channel.recv_exit_status()
    return code, _cap(out), _cap(err)


def _normalize_tags(raw: str) -> str:
    """Extract a clean ``vX.Y.Z`` tag from an arbitrary version string."""
    m = re.search(r"v\d+\.\d+\.\d+", raw or "")
    if m:
        return m.group(0)
    cleaned = (raw or "").strip().lstrip("v")
    if not cleaned:
        cleaned = "latest"
    return cleaned


def _download_script(
    os_arch: str,
    dest: str,
    *,
    server_url: str,
    version: str | None = None,
    verify: bool = True,
) -> str:
    """Bash that downloads the prebuilt binary + (optional) SHA256SUMS.

    Mirrors scripts/install-connector.sh: curl -fsSL to a temp file, chmod +x,
    atomic mv into ~/.local/bin so a running reader keeps the old inode.
    """
    asset = f"tomo-connector-{os_arch.replace(' ', '-')}"
    tag = _normalize_tags(version) if version else "latest"
    if tag == "latest":
        url = LATEST_URL.format(asset=asset)
    else:
        url = TAG_URL.format(version=tag, asset=asset)

    download = (
        "TMP=$(mktemp \"${{TMPDIR:-/tmp}}/tomo-connector.XXXXXX\")\n"
        "trap 'rm -f \"$TMP\" \"$TMP.sum\"' EXIT\n"
        "curl -fsSL {url} -o \"$TMP\" || {{\n"
        "  echo 'download failed' >&2\n"
        "  exit 1\n"
        "}}\n"
    ).format(url=shlex.quote(url))

    checksum = ""
    if verify:
        sums_url = SHA256SUMS_TAG_URL.format(version=tag)
        checksum = (
            "# optional SHA256 verification\n"
            "set +e\n"
            "curl -fsSL {sums} -o \"$TMP.sum\" 2>/dev/null\n"
            "CURL_RC=$?\n"
            "set -e\n"
            "if [ $CURL_RC -eq 0 ] && [ -s \"$TMP.sum\" ]; then\n"
            "  EXPECTED=$(awk '{{print $1}}' \"$TMP.sum\" | head -1)\n"
            "  ACTUAL=$(sha256sum \"$TMP\" | awk '{{print $1}}')\n"
            "  if [ -z \"$EXPECTED\" ]; then\n"
            "    echo '⚠ SHA256SUMS empty — skipping verification'\n"
            "  elif [ \"$EXPECTED\" != \"$ACTUAL\" ]; then\n"
            "    echo '✗ SHA256 checksum mismatch' >&2\n"
            "    rm -f \"$TMP\" \"$TMP.sum\"\n"
            "    exit 1\n"
            "  else\n"
            "    echo '✓ SHA256 verified'\n"
            "  fi\n"
            "else\n"
            "  echo '⚠ SHA256SUMS unavailable — skipping verification'\n"
            "fi\n"
        ).format(sums=shlex.quote(sums_url))

    script = (
        "command -v curl >/dev/null 2>&1 || {{ echo 'curl is required' >&2; exit 1; }}\n"
        "BIN_DIR=\"$HOME/{binsub}\"\n"
        "DEST={dest}\n"
        "mkdir -p \"$BIN_DIR\"\n"
        "if [ -x \"$DEST\" ]; then echo '→ Updating existing install'; fi\n"
        "echo '→ Downloading {asset} ({tag})…'\n"
        "{download}"
        "{checksum}"
        "chmod +x \"$TMP\"\n"
        "mv -f \"$TMP\" \"$DEST\"\n"
        "echo '✓ binary installed'\n"
    ).format(
        binsub=_BIN_SUBDIR,
        dest=shlex.quote(dest),
        asset=asset,
        tag=tag,
        download=download,
        checksum=checksum,
    )
    return script


def _systemd_user_unit() -> str:
    """User unit template — mirrors connector/deploy/tomo-connector.service."""
    return (
        "[Unit]\n"
        "Description=Tomo Connector\n"
        "After=network-online.target\n"
        "Wants=network-online.target\n"
        "\n"
        "[Service]\n"
        "Type=simple\n"
        "ExecStart=%h/.local/bin/tomo-connector run\n"
        "Restart=always\n"
        "RestartSec=5\n"
        "Environment=TOMO_CONNECTOR_HOME=%h/.tomo-connector\n"
        "StandardOutput=journal\n"
        "StandardError=journal\n"
        "\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    )


def _systemd_system_unit(*, bin_path: str, home: str) -> str:
    """System unit for root installs (no systemd --user session for uid 0)."""
    return (
        "[Unit]\n"
        "Description=Tomo Connector\n"
        "After=network-online.target\n"
        "Wants=network-online.target\n"
        "\n"
        "[Service]\n"
        "Type=simple\n"
        f"ExecStart={bin_path} run\n"
        "Restart=always\n"
        "RestartSec=5\n"
        f"Environment=TOMO_CONNECTOR_HOME={home}\n"
        "StandardOutput=journal\n"
        "StandardError=journal\n"
        "\n"
        "[Install]\n"
        "WantedBy=multi-user.target\n"
    )


def _install_service_script(dest: str, os_arch: str) -> str:
    """Bash: write + enable + start a systemd unit (Linux only).

    Root → system unit in /etc/systemd/system (``systemctl`` without ``--user``).
    Non-root → user unit under ``~/.config/systemd/user``.
    """
    os_name = os_arch.split()[0]
    if os_name not in _SUPPORTED_SYSTEMD:
        return (
            "echo \"ℹ systemd service skipped on ${os_name} — "
            "run 'tomo-connector service install' manually\"\n"
        )
    # Keep %h in the user unit — systemd expands it; do not substitute $HOME.
    user_unit = _systemd_user_unit().rstrip() + "\n"
    # dest is typically ``$HOME/.local/bin/tomo-connector`` (shell-expanded remotely).
    return f"""
command -v systemctl >/dev/null 2>&1 || {{
  echo '⚠ systemctl not found — connector installed; start manually with tomo-connector run'
  exit 0
}}
DEST_BIN={dest}
if [ "$(id -u)" -eq 0 ]; then
  echo "→ systemd system unit (root)"
  case "$DEST_BIN" in
    */.local/bin/tomo-connector)
      SYS_BIN=/usr/local/bin/tomo-connector
      mkdir -p /usr/local/bin
      if [ -x "$DEST_BIN" ] && [ "$DEST_BIN" != "$SYS_BIN" ]; then
        cp -f "$DEST_BIN" "$SYS_BIN" && chmod 755 "$SYS_BIN"
        DEST_BIN="$SYS_BIN"
      fi
      ;;
  esac
  CONN_HOME="${{TOMO_CONNECTOR_HOME:-$HOME/.tomo-connector}}"
  UNIT="/etc/systemd/system/{_UNIT_NAME}"
  cat > "$UNIT" <<EOF
[Unit]
Description=Tomo Connector
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=$DEST_BIN run
Restart=always
RestartSec=5
Environment=TOMO_CONNECTOR_HOME=$CONN_HOME
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF
  systemctl daemon-reload
  systemctl enable {_UNIT_NAME}
  systemctl start {_UNIT_NAME}
  systemctl is-active {_UNIT_NAME}
else
  echo "→ systemd --user unit"
  mkdir -p "$HOME/.config/systemd/user"
  UNIT_DIR="$HOME/.config/systemd/user"
  UNIT="$UNIT_DIR/{_UNIT_NAME}"
  cat > "$UNIT" <<'TOMOCONNECTOR'
{user_unit}TOMOCONNECTOR
  systemctl --user daemon-reload
  systemctl --user enable "$UNIT_DIR/{_UNIT_NAME}"
  systemctl --user start {_UNIT_NAME}
  systemctl --user is-active {_UNIT_NAME}
fi
"""


def install_via_ssh(
    *,
    ssh_host: str,
    ssh_port: int,
    ssh_user: str,
    ssh_password: str,
    ssh_key: str,
    name: str,
    server_url: str,
    arch: str | None = None,
    os_name: str | None = None,
    version: str | None = None,
    verify: bool = True,
    poll_timeout: float = _POLL_TIMEOUT,
    poll_interval: float = _POLL_INTERVAL,
    now: Callable[[], float] = _now,
    is_online: Callable[[str], bool] | None = None,
    store: Any | None = None,
) -> Result:
    """Install the connector on a remote host and register a tunnel workplace.

    Parameters mirror the API body plus a few knobs used by tests.

    ``store`` must expose ``create_workplace``, ``issue_pairing_code`` and
    ``get_workplace`` (defaults to :data:`app.services.store` singleton).
    ``is_online(wid)`` defaults to the live hub lookup.
    """
    log = Result({}, "failed")
    try:
        log.append(f"→ connecting to {ssh_user}@{ssh_host}:{ssh_port} …")
        client = ssh_exec.connect(
            {
                "ssh_host": ssh_host,
                "ssh_port": ssh_port,
                "ssh_user": ssh_user,
                "ssh_password": ssh_password,
                "ssh_key": ssh_key,
            }
        )
    except Exception as exc:
        raise InstallError("ssh", f"SSH connection failed: {exc}", retryable=True) from exc
    try:
        if os_name and arch:
            os_arch = f"{_normalize_os_arch(os_name, arch)}"
        else:
            os_arch = _remote_os_arch(client)
        log.append(f"✓ remote: {os_arch}")

        # 2. download prebuilt binary (curl on the remote, never through API).
        dest = f"$HOME/{_BIN_SUBDIR}/{_BIN_NAME}"
        code, out, err = _run_remote(
            client,
            _download_script(
                os_arch, dest, server_url=server_url, version=version, verify=verify
            ),
            timeout=120.0,
            label="download",
        )
        if out:
            log.append(out.strip())
        if code != 0:
            raise InstallError(
                "download",
                f"binary download/install failed (exit {code}): {err.strip() or out.strip()}",
                retryable=True,
            )

        # 3. systemd --user unit (Linux).
        code, out, err = _run_remote(
            client, _install_service_script(dest, os_arch), timeout=60.0, label="service"
        )
        if out:
            log.append(out.strip())
        if code != 0:
            raise InstallError(
                "service",
                f"systemd unit install failed (exit {code}): {err.strip() or out.strip()}",
                retryable=True,
            )
    finally:
        client.close()

    # 4. pair against this coordinator.
    if store is None:  # pragma: no cover - imported lazily to keep tests light
        from app.services import store as _store

        store = _store
    try:
        wp = store.create_workplace(
            {
                "name": name,
                "kind": "tunnel",
                "host": ssh_host,
                "ssh_host": ssh_host,
                "ssh_port": ssh_port,
                "ssh_user": ssh_user,
                "ssh_password": ssh_password,
                "ssh_key": ssh_key,
            }
        )
        wid = wp["id"]
        log.append(f"✓ registered tunnel workplace {wid}")
        wp2 = store.issue_pairing_code(wid) or wp
        code = (wp2.get("pairing_code") or "").strip()
        expires = wp2.get("pairing_expires_at") or 0
        if not code:
            raise InstallError(
                "pair", "no pairing code generated — connector cannot pair", retryable=False
            )
        log.append(f"✓ pairing code {code} (expires {expires:.0f})")
    except InstallError:
        raise
    except Exception as exc:
        raise InstallError("pair", f"could not prepare pairing: {exc}", retryable=False) from exc

    # Run pair remotely. TOMO_CONNECTOR_PAIR_AND_RUN starts `run` automatically;
    # when unavailable we fall back to starting the service unit right after.
    client = None
    try:
        log.append("→ pairing connector over SSH …")
        client = ssh_exec.connect(
            {
                "ssh_host": ssh_host,
                "ssh_port": ssh_port,
                "ssh_user": ssh_user,
                "ssh_password": ssh_password,
                "ssh_key": ssh_key,
            }
        )
        pair_cmd = (
            f"export PATH=\"$HOME/{_BIN_SUBDIR}:$PATH\"\n"
            f"TOMO_CONNECTOR_PAIR_AND_RUN=1 tomo-connector pair "
            f"--code {shlex.quote(code)} --server {shlex.quote(server_url)}"
        )
        code_rc, out, err = _run_remote(client, pair_cmd, timeout=90.0, label="pair")
        _ensure_pair_ok(code_rc, out, err)
        log.append((out or err).strip())
    except InstallError:
        raise
    finally:
        if client is not None:
            client.close()

    # 5. wait for the connector socket to register.
    connected_at = _wait_online(
        wid,
        poll_timeout=poll_timeout,
        poll_interval=poll_interval,
        now=now,
        is_online=is_online,
        store=store,
        log=log,
    )
    log.append("✓ connector online" if connected_at else "⏳ connector not yet online")
    status = "connected" if connected_at else "pairing"
    wp = store.get_workplace(wid) or wp
    log.append("done")
    return Result(workplace=wp, status=status, log=log.log)


def _remote_os_arch(client: Any) -> str:
    code, out, err = _run_remote(client, "uname -s; uname -m", timeout=30.0, label="detect")
    if code != 0:
        raise InstallError(
            "detect", f"uname failed (exit {code}): {err.strip() or out.strip()}", retryable=True
        )
    lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
    if len(lines) != 2:
        raise InstallError("detect", f"unexpected uname output: {out}", retryable=True)
    return _normalize_os_arch(lines[0], lines[1])


def _ensure_pair_ok(rc: int, out: str, err: str) -> None:
    """Raise when the pair command clearly failed."""
    combined = f"{out}\n{err}"
    fatal_markers = (
        "error:",
        "failed:",
        "not paired",
        "401",
    )
    lowered = combined.lower()
    if rc != 0 or any(m in lowered for m in fatal_markers):
        raise InstallError(
            "pair",
            f"pairing failed (exit {rc}): {combined.strip()[:2000]}",
            retryable=True,
        )


def _wait_online(
    wid: str,
    *,
    poll_timeout: float,
    poll_interval: float,
    now: Callable[[], float],
    is_online: Callable[[str], bool] | None,
    store: Any,
    log: Result,
) -> float | None:
    """Wait until the connector registers (or timeout); returns connected_at."""
    if is_online is None:
        from app.workplaces.hub import hub

        is_online = hub.is_online
    deadline = now() + poll_timeout
    last_at: float | None = None
    while True:
        # Hub is authoritative when present; DB fallback for tests.
        connected_at: float | None = None
        if is_online(wid):
            try:
                wp = store.get_workplace(wid) or {}
                raw = wp.get("connector_connected_at") or 0
                if float(raw or 0) > 0:
                    connected_at = float(raw)
            except Exception:
                connected_at = now()
        if connected_at is not None:
            last_at = connected_at
            break
        if now() >= deadline:
            break
        time.sleep(poll_interval)
    return last_at


__all__ = [
    "InstallError",
    "Result",
    "install_via_ssh",
    "LATEST_URL",
    "RELEASE_REPO",
    "TAG_URL",
    "SHA256SUMS_TAG_URL",
]
