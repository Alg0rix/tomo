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
