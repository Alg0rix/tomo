# Target a workplace in tool calls

Use this when a task must run on a specific tunnel or SSH machine, when an agent
can reach several workplaces, or when a tool result looks like it ran on the
wrong host. For installing or repairing the connector itself, load
[Connections](connector.md).

## Pick the ID first

```text
list_workplaces(kind="tunnel", online_only=true)
list_workplaces(kind="all")
```

Pass the returned `id` (for example `wp_ab12cd`), not a guess from the user's
wording. Names and hostnames also match, but an ambiguous partial match
selects nothing and the call silently falls through to the default workplace.
`list_workplaces` only returns workplaces in the agent's scope. A host outside
that scope cannot be targeted by name. Change the agent's scope through
[Agents and models](agents-models.md) only when the user authorizes it.

## How each call picks its host

Tomo resolves the workplace separately for every tool call, in this order:

1. **Host named for the call or turn**: `bash(workplace=...)` (alias
   `workplace_id`), or a user message ending in a workplace token
   (`... on aio-serv`, `... at wp_ab12cd`, or just `... aio-serv`).
2. **Turn/session binding**: the chat's selected folder/workplace, or
   `register_workplace(use_now=true)` earlier in this turn.
3. **Agent default**: with `list`/`all`/`all_tunnels` scope and several
   workplaces, the first *online* tunnel, then the primary `workplace_id`, then
   the first allowed. With `single` scope, the primary.
4. **Nothing resolved**: the local sandbox `$TOMO_WORK/<agent>` on the
   coordinator. When the chat chose "Tomo work dir", the agent's local
   workplaces are skipped but tunnels/SSH still resolve.

A name that does not match (typo, ambiguous, out of scope) is ignored and the
next rule decides. It does not produce an error, so always verify the host.

## Which tools take a `workplace` argument

| Tool | Per-call selector | Otherwise uses |
| --- | --- | --- |
| `bash` (foreground and `background=true`) | `workplace="<id>"` | rules 2–4 |
| `read_file`, `write_file`, `str_replace`, `patch`, `delete_file`, `list_dir`, `search_files`, `runpy` | none | rules 1 (message token), 2–4 |
| `process` (`list`/`status`/`kill`) | none | rules 1 (message token), 2–4 |
| `portal` | explicit `<workplace-id>:<path>` in `src`/`dst` | n/a |

The `bash` argument applies to **that one call only**. It does not re-point
later file tools, `runpy`, or `process`. Do not add `workplace` to tools whose
schema lacks it.

## Recipes

**Verify before acting** (always first on a remote host):

```text
bash(command="hostname; pwd; id -un", workplace="<id>")
```

Compare the hostname with the `list_workplaces` entry. A coordinator hostname
means the selector did not match.

**Shell work on a non-default host**: pass `workplace="<id>"` on every `bash` call.

**File edits on a non-default host**: the file tools follow the turn's
workplace, so choose one:

- The turn is already bound to that host (the user's message named it, or the
  chat's workplace is set to it). Use the file tools normally and verify with
  `bash(command="pwd", workplace="<id>")`.
- Otherwise, do the file work through `bash(..., workplace="<id>")`
  (`cat -n`, `sed -n`, a heredoc write, `grep -rn`).
- Or `delegate` to an enabled agent whose default workplace is that host, and
  put the workplace ID and paths in the brief.

Do **not** call `register_workplace(kind="tunnel")` to switch hosts. It creates
a new, unpaired tunnel record rather than reusing the existing one.

**Background jobs on a non-default host**: `process` has no selector, so its
job IDs only resolve on the turn's workplace. When the host is not
turn-bound, start the job in a way `bash` can inspect:

```text
bash(command="nohup <cmd> > /tmp/job.log 2>&1 & echo $!", workplace="<id>")
bash(command="kill -0 <pid> && echo running; tail -n 40 /tmp/job.log", workplace="<id>")
```

Use `bash(background=true, workplace=...)` plus `process` only when that host is
also the turn's workplace.

**Copy files between hosts**: use `portal` with explicit IDs on both sides, e.g.
`src="<wp-a>:build/out.tar"`, `dst="<wp-b>:deploy/out.tar"`. See
`references/connector/operations.md` for transfer polling.

## Failure signals

- `Error: tunnel workplace '<name>' is offline`: the connector is not
  connected. Repair it with `references/connector/troubleshooting.md`. Do not
  rerun the task on another host.
- Output shows the coordinator's hostname or the `$TOMO_WORK` path: the
  selector did not match. Re-check the ID against `list_workplaces`.
- `bash` timeouts are capped at 120 s per call. For longer work, use the
  background recipe above.

Report the workplace ID and observed hostname alongside results from a remote
machine.
