# Development and release scripts.

- `install.sh` — bootstrap managed install under `~/.local/share/tomo/app` and a systemd `--user` unit (`tomo.service`).
- `install-connector.sh` — install/update a prebuilt `tomo-connector` from GitHub Releases into `~/.local/bin` (re-run replaces binary + restarts user service if enabled).
