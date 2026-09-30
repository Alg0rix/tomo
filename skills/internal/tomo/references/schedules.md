# Schedules and background work

Use this when creating, changing, pausing, resuming, deleting, or diagnosing an
agent job. Scheduling a future task is distinct from running it now and from
starting a shell background process.

## Make the job self-contained

Establish what should happen, which configured agent/workplace performs it,
when it should fire, and whether it repeats. Include required paths, resource
IDs, inputs, output location, and a useful completion condition in `message`.
A scheduled turn starts in a fresh scheduler session without this chat's context.
Do not write prompts such as “do the thing we discussed above”.

| Form | Meaning |
| --- | --- |
| `30m`, `2h`, `1d` | One-shot after a duration |
| `every 30m`, `every 2h` | Recurring interval |
| `0 9 * * *` | Five-field cron, daily at scheduler 09:00 |
| ISO datetime | One-shot at a specific time; use an explicit offset when needed |

Timezone follows the deployment/scheduler configuration; naive times do not
inherit the user's timezone automatically. Confirm the intended timezone and
read the returned `next_run` before reporting the date/time. Do not invent a
CLI `--timezone` flag or a new settings key that the installation does not have.

## Configure through CLI

```bash
tomo config schedules list --json
tomo config schedules schema --json
tomo config schedules create --data @/absolute/job.json --json
tomo config schedules show <returned-id> --json
tomo config schedules pause <job-id> --json
tomo config schedules resume <job-id> --json
```

Create data includes `name`, actual `agent_id`, `schedule`, and a complete
`message`; `repeat_times` sets a run cap when appropriate. Inspect the request
schema for limits and update fields. Use `update <id>` on an existing job; delete
only for a removal request. Keep the same job ID for schedule/message repairs.
CLI writes do not refresh active scheduler jobs in another process; apply the
coordinator's actual lifecycle for changed runtime scheduling when needed.

## Use the runtime scheduler when available

The enabled `schedule` tool supports `create`, `list`, `update`, `pause`,
`resume`, `remove`, `run`, and `runs`. Runtime create uses `schedule`, `message`,
optional `agent_id`, `name`, and **`repeat`**, not CLI `repeat_times`.

```text
schedule(action="list", include_disabled=true)
schedule(action="runs", schedule_id="<actual-job-id>", limit=10)
```

`run` fires immediately; it is not a dry run and can cause the same side effects
as the scheduled prompt. Use it when an immediate run is requested or appropriate
within the task. `pause` preserves the job; `remove` deletes it. Neither proves
an already-running agent/process was stopped. Inspect its run/session separately.
Scheduled jobs should not recursively create more schedules unless requested.

## Verify and repair

Read the saved message, target agent, enabled/state, schedule expression,
`next_run`, and repeat/run count. For execution requests, inspect run history,
status/error, session output, and the expected artifact/effect. A scheduled row
or `next_run` alone is not proof the job executed.

For a missed run, check that the coordinator/scheduler is running, time and
claim state are sensible, the job is not paused/completed, and its agent/model/
workplace remains usable. For duplicate effects, inspect run history before
repeating execution; do not create duplicate jobs as a retry mechanism.
Transient shell process jobs use the `process` tool and its actual host/job ID;
they are not durable schedule definitions and may disappear after restart.
