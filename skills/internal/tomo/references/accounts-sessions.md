# Accounts, API keys, sessions, approvals, and artifacts

Use this for login-account changes, API access, session/history questions,
approval behavior, or delivering outputs. Local CLI administration and a logged-in
Tomo user's session identity are separate contexts.

## Manage accounts

```bash
tomo config users list --json
tomo config users schema --json
tomo config users create --data @/absolute/account.json --json
tomo config users update <user-id> --set display_name='Operator' --json
```

Create requires `username` and `password`; usernames accept letters, digits, and
underscores. Use the schema for length limits and a protected input channel for
passwords. Update supports display name, enabled state, and password; it is not
a username/role migration command. Blank update passwords preserve the hash.

`TOMO_ADMIN_PASSWORD` seeds an empty user table; changing that environment value
does not change an existing account's password. Use an account update for that
request. The last enabled account cannot be disabled/deleted. Disabling an account
and deleting it are different actions; inspect linked access before removal and
verify the relevant account state afterward.

## Manage account API keys

```bash
tomo config api-keys list --json
tomo config api-keys create --set user_id=<actual-user-id> --set name=automation --json
tomo config api-keys delete <actual-key-id> --json
```

Create returns plaintext `token` once; subsequent reads return public metadata,
not recoverable credentials. Deliver/store it through the intended protected
channel and do not repeat it in a final summary. Rotation creates a new key,
updates the intended client, verifies it, then revokes the old key when requested.
A failed client update is not a reason to delete the working key immediately.

Account API keys authenticate admin/API operations. Connector tokens authenticate
one paired tunnel and are not admin credentials. Local `tomo config` does not need
either token. Do not create an account key merely to issue a local pairing code.

## Sessions and history

Use the current session identity and available web/runtime controls for chat,
participants, workplace selection, reasoning effort, attachments, and history.
`session_search` can retrieve prior messages when enabled; `list_artifacts`
inspects the current chat's saved outputs. A configured agent's persistent model/
workplace assignment does not establish a session override's value.

The configuration CLI has no `sessions`, `chat`, session-reasoning, or history
resource. Do not fabricate commands or directly edit session rows to simulate
chat/runtime actions. Inspect the actual supported interface if a session-specific
operation is requested. Runtime/API ownership checks determine which logged-in
user can inspect a session; OS CLI access is not evidence that a chat belongs to
the current user. Clearing chat context, deleting a session, and deleting an
artifact are distinct requested operations with different consequences.

For one bounded peer handoff use `delegate` when enabled; for a worker team use
[swarm](swarm.md). Configuring a new agent alone does not run it, start a team,
or switch the current chat's lead model.

## Approvals

Read [channels/settings](channels-settings.md) for stored approval defaults and
session overrides. An approval request belongs to the pending action/session;
changing global settings is not approving that action. Respect the actual gate
and explain the blocker. Do not grant broader access, change Auto mode, or delete
deny rules as an incidental workaround.

## Deliver outputs through the session

After creating a requested report/export/page/image, use the enabled
`save_artifact` tool to register the output in the current session. Supply
`source_path` for a produced file or `content` for UTF-8 text, with useful filename,
MIME type/title when appropriate. A workplace file alone is not necessarily in
the Files panel. A remote path must be interpreted through the actual tool's
workplace capabilities; do not label a target path as a coordinator-local file.

Use `list_artifacts` to confirm the saved session output and provide its returned
link/location. Sharing an artifact publicly is a separate action from saving it;
use the actual authorized sharing control. Do not turn on global `public_history`
or create a public share just because the user requested a private report.
Record the artifact/evidence in the final response without exposing credentials,
private unrelated session contents, or another user's files.
