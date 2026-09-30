# Troubleshooting and protocol

## Diagnose from observable status

| Symptom | Checks and recovery |
|---------|---------------------|
| Binary not found | Check PATH and installed destination; invoke the absolute binary path |
| `not paired` | Check account and `TOMO_CONNECTOR_HOME`; pair under the service's account/state directory |
| Invalid/expired pairing code | Generate a fresh code on the intended tunnel record; check disabled state and coordinator URL |
| Pair HTTP 429 / WS close 4408 | Rate limit; stop repeated attempts and retry after the window instead of looping pairing |
| Paired but offline | Start `run` or the service; local status only checks saved state |
| Unauthorized / close 4401 | Check disabled/deleted workplace, wrong server, or stale token; re-pair if appropriate, then restart |
| Connected to unexpected host | Compare registry ID, reported hostname, remote `hostname`, and account; check duplicate connectors using the same state |
| `systemctl --user` cannot connect | Confirm non-root login/session and user service manager; root uses a system unit; consider linger for the intended user |
| Service runs but wrong root/state | Inspect unit and drop-ins, ExecStart, account, and service environment; shell exports are insufficient |
| TLS/DNS/connection failure | Test coordinator reachability from target, certificate trust, outbound firewall, and proxy settings |
| Pair works but WS fails | Proxy must pass WebSocket upgrade and Authorization to `/api/connector/ws`; check route prefix and idle timeouts |
| Tool says path escapes directory | Obtain actual connector root and use a path within it; change persistent root only if the task calls for that access |
| `bash` / `python3` missing | Install the needed interpreter on the target or choose an available capability |
| RPC timeout/disconnect | Check command timeout, live socket and logs; determine whether the operation executed before retrying a mutation |
| Unknown process job after restart | In-memory registry was lost; inspect the intended host's process/output state |
| MCP failure | Follow the separate MCP reference; tunnel pairing does not repair an MCP server |

Collect short, relevant logs on the target:

```bash
tomo-connector status
tomo-connector service status
systemctl --user cat tomo-connector.service
journalctl --user -u tomo-connector.service -n 100 --no-pager
```

For root's system unit omit `--user`. Without systemd, inspect the running
connector's stderr/supervisor logs. Events include `run.not_paired`, `ws.dial`,
`ws.disconnected`, `exec.start`, `exec.done`, and `rpc.cache_hit`. Execution logs
can include snippets of command parameters/results; redact secrets and private
content before sharing. Do not dump environment or credential files.

Repair the demonstrated failure and repeat a harmless target probe. Re-pairing
is not needed for every network interruption. The client retries failed
connections with exponential backoff and jitter capped at 30 seconds.

## Coordinator CLI checks

Run `tomo workplaces list --json` and `show <id> --json` in the coordinator's
installation to inspect saved configuration. For an expired code, use
`pairing-code <id> --json` on the same enabled tunnel. CLI `online: null` means
live connectivity is unavailable to that process, not that the target is
necessarily offline. A saved `connected` status may also be stale. Verify with
the running server's workplace tool and a harmless target probe.

If CLI reports a missing database, check the OS account, TOMO_HOME/TOMO_DB_PATH,
and whether Tomo runs in Docker. Do not recreate the workplace/database to fix
an installation-context mistake. See [CLI configuration](../configuration-cli.md).

## Admin API map

These are server HTTP routes, not agent tool names. Workplace management requires
authenticated Tomo access. Use existing authorized UI/API access; do not assume
that a connector bearer token is an admin credential.

| Route | Purpose |
|-------|---------|
| `GET /api/workplaces` | Registry/status |
| `POST /api/workplaces` | Create workplace (`name`, `kind`, and kind-specific fields) |
| `GET /api/workplaces/{id}` | Inspect workplace |
| `PUT /api/workplaces/{id}` | Update workplace configuration |
| `POST /api/workplaces/{id}/pairing-code` | New short-lived code |
| `POST /api/workplaces/{id}/connect` | Probe; cannot force an offline tunnel online |
| `POST /api/workplaces/{id}/disable` or `/enable` | Block/disconnect or permit authentication |
| `POST /api/workplaces/install-via-ssh` | Install with supplied SSH credentials and target settings |
| `DELETE /api/workplaces/{id}` | Remove registry entry and drop live connection |

## Wire protocol and retry limits

Pairing: `POST /api/connector/pair` with `pairing_code`, `device_name`, `platform`,
and `version` (optionally `local_ip`); success returns `ok`, `connector_token`,
`workplace_id`, and `workplace_name`. This endpoint uses the short-lived code,
not an admin session. Never expose the returned token in the final answer.

The Go client converts HTTP→WS or HTTPS→WSS and connects to
`/api/connector/ws` with `Authorization: Bearer <token>`, `X-Device-Name`,
`X-Platform`, `X-Tomo-Connector-Version`, and `X-Tomo-Caps: idempotent-replay`.
Successful authentication receives `hello_ok`; heartbeats/pings keep the
session visible. Legacy JSON `pair`/`hello` authentication is also accepted.

Protocol envelopes use `v: 1`. The server sends
`rpc_request {id, method, params}` and the client replies
`rpc_response {id, ok, result|error}`. Supported methods include execution,
file operations, `cwd_info`, process jobs, and base64 file chunks for portal.

The client caches in-flight/completed RPC results by request ID for about five
minutes in memory. When both sessions support replay, the server can hand off
pending requests during socket replacement. This is bounded replay protection,
not durable exactly-once delivery: restarts, expiration, and a manually retried
tool call with a new ID can re-execute work. Check side effects before retrying
payments, deployments, deletes, or other non-idempotent operations.

Only one current session is registered for a workplace; another connector with
the same credentials replaces the previous socket. Do not copy state between
machines to create multiple hosts. Hub/session state is process-local; deployment
must route pairing, status, and tool execution consistently to the process owning
the socket. Investigate worker/proxy routing if the connector looks online in
one request but unavailable in another.
