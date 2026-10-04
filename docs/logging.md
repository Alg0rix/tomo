# Inspecting runtime logs

Tomo writes console logs and structured JSONL to `$TOMO_HOME/logs/tomo.jsonl`.
Logging is configured before routes and bootstrap initialize. No separate service
or logging dependency is needed. Restart the server after updating/configuring.

## Web inspector

Open **System → Logs** (`/system#logs`) as an enabled administrator.
The inspector uses authenticated SSE, loads the last 100 matching records from
retained files, and keeps at most 500 records in the browser. Filter level/type,
session/request ID and event; apply filters to reload the stream. Expand a record
for JSON correlation fields and traceback. Pause closes the stream; resume loads
recent history (not guaranteed full catch-up). Follow latest controls auto-scroll;
Clear view does not delete server files. Navigation away closes the connection.
Reconnection reloads recent history and deduplicates retained rows.

Both `/api/logs` and `/api/logs/stream` require admin access, including API keys.
Logs are global runtime diagnostics, not a per-user history feed. Stream access
is rechecked for disabled accounts/role changes. File reads and history queries
are bounded by configured retention; there is no arbitrary file-path parameter.

## Live inspection

```sh
tomo logs -f                           # last 100 matching records, then live
tomo logs -f --type llm                # provider calls, retries, failures
tomo logs -f --type tool               # execution and MCP tool calls
tomo logs --level WARNING -n 200       # warnings and higher, including backups
tomo logs -f --session SESSION_ID      # trace chat, agent, LLM and tools together
tomo logs --request REQUEST_ID --json  # correlate with HTTP X-Request-ID header
tomo logs --event failed --json
tomo logs -f --type session_title
```

Types: `http`, `chat`, `agent`, `llm`, `tool`, `scheduler`, `telegram`,
`session_title`, `mcp`, `plugin`, `system`. Other module logs retain their logger
name as type. All existing `app.*` logger output is captured, not just the
instrumented boundaries. DEBUG messages require `TOMO_LOG_LEVEL=DEBUG`.

Instrumented boundaries:

- HTTP request start/end, status, route template, request ID and total duration
  (stream duration includes time delivering SSE).
- Chat stream and agent turn start/completion/failure/cancellation/early closure.
- LLM round and OpenAI-compatible/Codex provider completion/stream lifecycles,
  model/provider, latency; existing retry warnings remain visible.
- Shared asynchronous tool dispatch, including MCP and Telegram file sends;
  exceptions and `Error:` results are classified as failures.
- Schedule fire lifecycle and returned failed/skipped status.
- Telegram inbound update and channel turn lifecycle, existing polling/delivery logs.
- Title upgrade eligibility, generation, discard, failure and persistence.
- Runtime startup/shutdown and formerly silent startup skill-sync/MCP-close failures.

JSON records include UTC timestamp, severity, type, logger, PID, event and duration;
when available: session/agent, request/operation IDs, tool name and schedule ID.
Context flows to child tasks and worker threads without logging call arguments.
A completed boundary means its function/stream returned normally, not necessarily
that the model's answer was semantically successful; inspect error events too.

## Rotation and retention

Set environment variables or put them in `$TOMO_HOME/.env`:

```dotenv
TOMO_LOG_LEVEL=INFO
# Defaults: 10 MiB per file, five backups (about 60 MiB total).
TOMO_LOG_MAX_BYTES=10485760
TOMO_LOG_BACKUP_COUNT=5
# Optional override:
# TOMO_LOG_DIR=/private/path/logs
```

Positive size/count are required. Backups are `tomo.jsonl.1` (newest) through `.5`.
CLI reads retained backups oldest-first and follows the current file across
rotation. Live tail is best-effort, not a guaranteed log transport if several
rotations happen between polls. Use one server writer per log directory;
stdlib rotation does **not** coordinate multiple workers/processes.

New log files/backups are mode 0600; new log directories are mode 0700.
HTTP logs exclude query strings, raw paths, headers and bodies. New runtime
lifecycle logs exclude prompts, tool arguments/results and generated titles.
Existing module messages and exception tracebacks can still contain sensitive
values: treat logs as private, don't publish them unredacted. Third-party loggers
and Uvicorn access logs remain under their own console logging configuration.
