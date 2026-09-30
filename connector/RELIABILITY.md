# Connector reliability

The JSON RPC protocol and method result fields remain compatible with existing
Tomo servers. Requests require protocol version `1` and a nonempty request ID.
Request IDs and method names are limited to 128 bytes.

- Stdout and stderr retain at most 64 KiB each, with a `[truncated]` marker.
  Remaining output is drained without keeping it in memory. Background output
  snapshots are synchronized, and final status is published after output drains.
- At most 16 background jobs run at once. At most 128 jobs are retained. Finished
  jobs expire after 15 minutes; expired entries are removed during job operations.
  Job IDs are random to avoid collisions after a daemon restart.
- RPC execution uses eight workers and a queue of 32 requests across reconnects.
  Full queues and caches return an `ok: false` response beginning with `busy:`.
- A ping is sent every 25 seconds. Without a pong/heartbeat acknowledgement for
  75 seconds the socket closes and reconnects. Each write has a 10-second deadline
  and its own connection lock. A failed write closes that connection.
- On Linux and macOS, timeout and explicit job kill send TERM to the process group,
  then KILL after 200 ms. Children that deliberately leave the group are outside
  this mechanism. Windows uses `taskkill /T /F`, with direct process kill as a
  fallback. Output pipe waiting is bounded by a two-second `WaitDelay`.
- Routine logs contain RPC IDs, method names, timing, and status. They omit RPC
  payloads, output, pairing codes, and pairing response bodies. The logger also
  redacts known payload/credential fields defensively.

## Replay and restart

RPC intent and completed results are atomically written and synced under
`$TOMO_CONNECTOR_HOME/rpc-journal/<scope>/` (default home: `~/.tomo-connector`).
The scope includes server, workplace, and token identity. Directories use mode
0700 and records use 0600 on Unix; results may contain file or command output.
An OS lock prevents two connector processes from sharing the same journal.
Unix also syncs the directory after rename; Windows syncs the record file.

Completed requests replay their saved response without executing again, including
across daemon restarts. Reusing an ID with different parameters is rejected.
If intent exists but completion was interrupted, the connector returns an
`execution status uncertain` error and does not execute the request again.
Inspect the command/file effects before submitting a new request ID; this is
not an exactly-once transaction with the underlying shell or filesystem.
Background jobs are not restored after a daemon restart.

Records expire after 15 minutes (after completion for completed requests).
Replay guarantees apply within that retention window. Expired records are pruned
on startup and new requests. The cache retains at most 256 records and 64 MiB
of encoded responses, reserving space for error records. Responses above 12 MiB
or the remaining cache budget return a saved error stating execution completed
but the result is unavailable. A corrupt journal prevents startup rather than
silently allowing mutations to execute twice. Intent persistence failure prevents
execution; completion persistence failure returns an uncertain-status error.

The server retains disconnected sessions' pending RPCs for 90 seconds while
reporting them offline. A replacement session asynchronously replays those
requests. Completed/expired callers are removed even after session adoption.
Each server session allows at most 64 pending RPCs.

## Verification

From `connector/`, run `go test -race ./...` and `go vet ./...`.
From the repository root, run:

```sh
.venv/bin/python -m pytest -n 0 -q tests/unit/workplaces/test_hub_and_pairing.py tests/unit/runtime/tools/test_tunnel_rpc.py
```
