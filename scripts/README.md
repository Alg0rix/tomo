# Development and release scripts.

- `install.sh` — bootstrap managed install under `~/.local/share/tomo/app` and a systemd `--user` unit (`tomo.service`).
- `install-connector.sh` — install/update a prebuilt `tomo-connector` from GitHub Releases into `~/.local/bin` (re-run replaces binary + restarts user service if enabled).
- `benchmark_retrieval.py` — isolated Markdown/SQLite lexical-recall fixture; reports Recall@5, MRR@5, no-match accuracy and latency (`.venv/bin/python scripts/benchmark_retrieval.py`).
