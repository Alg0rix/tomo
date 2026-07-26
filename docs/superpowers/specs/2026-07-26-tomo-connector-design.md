# Tomo Connector — Design (v1)

**Date:** 2026-07-26  
**Status:** Implemented (Go connector + FastAPI hub)

## Goal

Make `kind=tunnel` workplaces real: a lightweight agent on a remote device
opens an **outbound WebSocket** to Tomo, pairs once, stays connected, and runs
the same tools (`bash`, `read_file`, `write_file`) as local workplaces —
behind NAT without inbound ports or SSH.

## Components

| Piece | Role |
|-------|------|
| Tomo server (`app/workplaces/hub.py`, `app/api/connector.py`) | Pairing, token storage, live session registry, RPC dispatch |
| Go binary (`connector/`) | `tomo-connector pair\|run\|status\|logout` |
| Tool backends | Route to hub when agent workplace is tunnel + online |

## Protocol (JSON, `v: 1`)

Path: **`/api/connector/ws`** (no session cookie; auth is pair code or token).

### Pair (first time)

```json
→ {"v":1,"type":"pair","code":"X7KQ2M","hostname":"pi","version":"0.1.0"}
← {"v":1,"type":"pair_ok","workplace_id":"wp_pi","token":"<long-lived>"}
```

### Hello (reconnect)

```json
→ {"v":1,"type":"hello","token":"…","hostname":"pi","version":"0.1.0"}
← {"v":1,"type":"hello_ok","workplace_id":"wp_pi"}
```

### Heartbeat

```json
→ {"v":1,"type":"heartbeat"}
← {"v":1,"type":"heartbeat_ack"}
```

### RPC

```json
← {"v":1,"type":"rpc_request","id":"…","method":"bash","params":{"command":"pwd"}}
→ {"v":1,"type":"rpc_response","id":"…","ok":true,"result":"/home/…"}
```

Methods: `bash`, `read_file`, `write_file`, `ping`, `cwd_info`.

## Status model

| Status | Meaning |
|--------|---------|
| `pairing` | Code issued; waiting for connector |
| `connected` | Live WebSocket on hub only |
| `offline` | Paired or not; no live socket |
| `later` | Legacy; unused for new tunnels |

API cannot set tunnel status to `connected` without the hub.

## Data

On `workplaces`:

- `pairing_code`, `pairing_expires_at` (TTL ~30 min, single active code)
- `connector_token` — Fernet ciphertext (`enc:v1:`)
- `connector_last_seen_at`, `connector_version`, `connector_hostname`

## Threat model (short)

| Threat | Mitigation |
|--------|------------|
| Guess pairing code | Short TTL; rate-limit pair/hello; large alphabet |
| Token theft at rest (server) | Fernet via `$TOMO_HOME/.secret_key` |
| Token theft on device | State file mode `0600` under `~/.tomo-connector` |
| Path escape on device | Jail under work root; reject `..` / absolute paths |
| Long bash | Timeouts on connector + RPC wait |
| Fake “connected” UI | Status only from hub live map |
| MITM | Prefer HTTPS/WSS in production; document TLS |

## Non-goals (v1)

- Fyne desktop GUI  
- Portal file bridge product  
- Replacing SSH workplaces  
- Multi-connector load-balancing per workplace (last session wins)

## Operator notes

- Do not start Tomo from the connector; human runs the server.
- Production: terminate TLS in front of uvicorn; allowlist server URL on the device if policy requires.
