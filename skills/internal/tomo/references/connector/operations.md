# Workplace tools and transfers

## Resolve and verify the host

```text
list_workplaces(kind="tunnel")
list_workplaces(kind="tunnel", online_only=true)
bash(command="hostname; pwd; id", workplace="<actual-workplace-id>")
```

Use the returned registry ID to avoid ambiguous names. If the target is absent,
check agent workplace scope (`single`, `list`, `all_tunnels`, `all`), assignment,
and pairing. If offline, repair it before running work. Do not use a local shell
as if it were the requested remote machine.

## Tool surface

| Agent tool | Connector RPC | Notes |
|------------|---------------|-------|
| `bash` | `exec_bash` | Foreground shell; `background=true` starts a job |
| `runpy` | `exec_python` | Requires remote `python3` |
| `read_file`, `write_file` | Same method names | Paths relative to connector root, or absolute within it |
| `str_replace`, `patch`, `delete_file`, `search_files` | Same method names | Use the actual enabled tool schema |
| `process` | `process_list`, `process_status`, `process_kill` | Jobs started by the connector process |
| `portal` | Binary file reads/writes through the bridge | Copies across workplaces/coordinator staging |

These RPC names are implementation details, not additional callable agent tools.
Use only the tools actually provided. Only `bash` declares a `workplace`
parameter; the other tools follow the turn's bound workplace. Load
`references/workplaces.md` for the resolution order and recipes for file edits
and background jobs on a non-default host. Do not create a duplicate record
merely to change the active target.

Verify remote `pwd` and file-tool root before using paths. Shell scripts execute
with `bash -s` on the connector, while local tool behavior can differ. Shell
`cd` can change directory for that command and does not persistently change the
file-tool root. Runtime code may query `cwd_info` to obtain the connector root.
Do not assume a coordinator filesystem path exists on the target.

The executor defaults to 60 seconds and accepts positive timeouts up to 600
seconds; out-of-range values fall back to the default. Public tool schemas can
advertise narrower limits; follow the provided schema. Foreground stdout and
stderr are each truncated to 64 KiB. A successful RPC can contain a nonzero
`exit_code`, or `-1` on timeout. Inspect the command result before claiming success.

## Background work

```text
bash(command="<authorized long-running command>", background=true, workplace="<id>")
process(action="status", id="<returned-job-id>")
process(action="list")
process(action="kill", id="<returned-job-id>")
```

Keep the same workplace binding for subsequent process calls; IDs belong to
the connector that started the job. Poll the returned job ID until completion
when the task needs its result. Use kill only for the intended job. Job tracking
and cached RPC results are in memory and do not survive connector restart;
inspect actual OS processes/output before restarting a lost job that may still
have produced side effects. Use a persistent OS supervisor for durable services.

## Portal

Locations are `/_portal/<name>/relative/path` on coordinator staging and
`<workplace-id>:<path>` on a registered workplace. Use actual IDs and paths:

```text
portal(action="copy", src="<wp-id>:reports/output.csv", dst="/_portal/reports/output.csv")
portal(action="copy", src="/_portal/input/data.csv", dst="<wp-id>:data/input.csv")
portal(action="copy", src="<source-id>:build/app.bin", dst="<destination-id>:deploy/app.bin", background=true)
portal(action="status", id="<returned-transfer-id>")
portal(action="list")
portal(action="cancel", id="<returned-transfer-id>")
```

Small copies can complete synchronously; large/forced-background copies return a
transfer ID. Inspect the result and poll that ID to completion, then verify the
destination size/content or checksum when relevant. Transfer through portal
instead of embedding binary data in chat. Both workplaces must be accessible,
and connector file paths must obey their roots. `/_portal/...` is bridge syntax,
not a directory to assume exists on every remote host.
