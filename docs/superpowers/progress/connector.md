# Tomo Connector — Progress

**Spec:** `docs/superpowers/specs/2026-07-26-tomo-connector-design.md`  
**Language:** Go binary under `connector/` (`tomo-connector`)

## Status

| Slice | Item | State |
|-------|------|-------|
| 1 | Protocol + WS hub + DB fields + pairing API | done |
| 2 | Go connector pair/run/reconnect + RPC | done |
| 3 | Tool backends route tunnel RPC | done |
| 4 | UI pairing + status (no “connector later”) | done |
| 5 | Docs + pytest | done |

## Log

- 2026-07-26: Implemented hub (`app/workplaces/hub.py`), pairing helpers, schema columns, `/api/connector/ws`, `POST …/pairing-code`.
- 2026-07-26: Go connector with gorilla/websocket; state in `~/.tomo-connector`.
- 2026-07-26: bash/read_file/write_file use `tunnel_rpc`; agent loop runs tools via `asyncio.to_thread` to avoid WS deadlock.
- 2026-07-26: Workplaces UI shows pairing code, install snippet, online/offline badges.
- 2026-07-26: **Connector protocol pass** — HTTP `POST /api/connector/pair`; Bearer + device headers on WS; idempotent-replay cache + hub grace; ambiguous-free codes; pair saves then `run` reconnects with jittered backoff.
- 2026-07-26: **Connector op surface** — connector RPC: `exec_bash`, `exec_python`, `read_file`, `write_file`, `read_file_b64`/`write_file_b64`; agent tools `bash`→`exec_bash`, new `runpy`→`exec_python`; structured results formatted to tool strings.
- 2026-07-26: **Gap close** — connector: `str_replace`, `delete_file`, `search_files`, process_* jobs; tunnel background bash; `runpy` in general skillset; SSH tools via **Paramiko** (`app/workplaces/ssh_exec.py` + `workplace_remote` router).
