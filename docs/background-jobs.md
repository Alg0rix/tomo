# Background jobs

Ask your agent to run a command in the background. Multiple commands can run in
the same conversation. Each receives a unique job ID, and keeps running after the
agent finishes its response, the browser closes, or you open another chat.

The runtime reads output while the command runs. When it finishes, the agent
continues the original task in the same conversation. If that conversation is
busy, its completed jobs wait for the current turn; results with the same
destination may be handled together. Completion does not automatically start a
swarm or insert another user message.

## Web

Each job has a card beside its original Bash call. Open **Workspace → Processes**
to see the conversation's jobs, retained logs, exit code, and process status.
**Go to original chat** links to the original history message. Snapshots keep
updating independently of the agent's response stream, and a new continuation
appears in the open conversation.

**Stop process** asks for confirmation and stops that command and its children.
Other jobs keep running. **Stop agent** pauses automatic continuation and leaves
the processes running. Send a new instruction in the original conversation to
resume admission of pending results.

An **Unknown** job has lost confirmed observation. It is never shown as a
successful command. Refresh or Stop can reconcile its original backend handle.
**Close monitoring** releases its active slot after confirmation; it does not
stop the remote command.

## Telegram

Background commands create persistent messages with **Refresh status**, **Latest
log**, and **Stop process** controls. These controls keep working after the
agent's activity message disappears. Long retained logs are sent as files.
Unknown jobs also offer **Close monitoring**.

Controls belong to the initiating person, chat, topic, account, bot, and original
conversation. Starting `/new` preserves the old job's destination. Reply to its
card to continue that original conversation; replies wait if another conversation
is currently using the Telegram chat slot. Completion replies also return to the
original topic and card. Opening that conversation in the web interface does not
change its delivery destination.

Process completion, agent continuation, and final delivery have separate states.
A blocked or uncertain Telegram send leaves the result in the web history. A
saved final that was never sent can be delivered after restart without invoking
the model or its tools again. An uncertain send stays **Unknown** and is not
automatically repeated. Revoked destinations, changed account links, deleted
conversations, and changed bot identity block delivery rather than rerouting it.

## Agent tools

Use `bash` with `background: true` to start a job. Use `process` with `action`
`list`, `status`, `log`, `kill`, or `close-monitoring` and the returned Tomo job
`id`. These tools only access the current authorized conversation and, in
Telegram, its captured actor and topic. `kill` and `close-monitoring` use the
current approval policy; closing monitoring additionally requires `confirm: true`.

## Limits and restart behavior

- Up to 16 active jobs per application, additionally subject to backend limits.
- Up to 1 MiB of combined retained stdout/stderr per job, expiring seven days
  after completion. Truncation and expiry are shown explicitly.
- Local commands, POSIX SSH hosts with Python 3, and updated Unix tunnel
  connectors support supervised background jobs. Remote backends must advertise
  `process_contract=1`; unsupported or older connectors are rejected without
  executing the command locally. Windows connector background supervision is
  unsupported in this version.
- Metadata survives restart; process survival is not guaranteed. Local jobs from
  the previous supervisor become **Interrupted**. Remote jobs become **Unknown**
  until their saved handles can be observed again. Unclaimed results wait for a
  new instruction; partially executed agent continuations are not replayed.
- Upgrading from the old process registry does not adopt its running commands or
  legacy SSH PID handles. Finish or stop those commands before upgrading.
- Deleting a conversation or its sole agent performs best-effort job cleanup.
  Offline remote commands may continue on their host.
- Run one supervisor-owning application process per database.

Interactive terminals and scheduled jobs keep their existing workflows.
