# Tomo Connector (Go)

Lightweight agent that opens an **outbound WebSocket** to the Tomo server and
runs the same tool surface (`bash`, `read_file`, `write_file`, …) for
`kind=tunnel` workplaces — no inbound ports or SSH required.

## Build

```bash
cd connector
go mod tidy
go build -o tomo-connector .
# optional install
# go install .
```

## Pair & run

On the Tomo UI: create a **tunnel** workplace → copy the pairing code.

```bash
./tomo-connector pair --code X7KQ2M --server http://coordinator:8787
# stays connected; Ctrl-C to stop

# later / after reboot (uses ~/.tomo-connector/state.json):
./tomo-connector run
./tomo-connector status
./tomo-connector logout
```

State is stored under `~/.tomo-connector/` (or `$TOMO_CONNECTOR_HOME`) with
file mode `0600`. Tool jail root defaults to `$TOMO_CONNECTOR_HOME/work`
(override with `TOMO_CONNECTOR_ROOT`).

## Protocol (v1)

WebSocket path: `/api/connector/ws`

| Direction | Type | Purpose |
|-----------|------|---------|
| C→S | `pair` | `{code, hostname, version}` first-time bind |
| S→C | `pair_ok` | `{workplace_id, token}` |
| C→S | `hello` | `{token, hostname, version}` reconnect |
| S→C | `hello_ok` | `{workplace_id}` |
| C→S | `heartbeat` | keep-alive |
| S→C | `rpc_request` | `{id, method, params}` |
| C→S | `rpc_response` | `{id, ok, result\|error}` |

Methods: `bash`, `read_file`, `write_file`, `ping`, `cwd_info`.
