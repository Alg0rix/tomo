# Multi-user implementation contracts

Approved source: [multi-user workplace access design](../superpowers/specs/2026-10-04-multi-user-workplace-access-design.md).
Current status: [implementation and release blockers](2026-10-04-multi-user-completion.md).
Verification: [test coverage and reproduction](2026-10-04-multi-user-integration-evidence.md).

## Identity and authorization

- `store.access` is `AccessService(store)`. `AccessDenied` rejects invalid identity/grants; `AccessUnavailable` also rejects pending teardown/unavailable backends. HTTP returns safe errors without host paths or secrets.
- Cookie/API-key authentication validates the current enabled account and role. No anonymous `web` principal or platform-role elevation from tool approvals.
- `require_user`, `require_admin`, `require_session` enforce policy. Admin control-plane authority does not inherit another user's private chats, keys or personal data.
- Managed personal/project roots are server chosen. Personal storage is not shareable; project owners can grant RO/RW access. Visible picker metadata excludes roots and credentials.
- Members need current agent/model/resource assignments. Shared coordinator availability does not grant access to unassigned models or other agents.

## Chat and execution context

- `set_chat_access` persists an owned active resource, enabled additional resources and mode. Member unrestricted mode requires a destination-specific grant AND explicit chat acknowledgement; it does not change the user's platform role. Trusted Admin chats default to host execution on accessible writable destinations without either setup step, including linked Telegram and root installations. Explicit Restricted selections and revocations are preserved.
- `resolve_context` returns immutable `ExecutionContext`; `revalidate` intersects current policy with its original ceiling. Missing execution identity fails closed.
- `require_tool` enforces current agent capabilities; `authorize_resource` enforces enabled read/write scope. Delegation narrows the parent ceiling.
- `execution_scope`, `bind_execution` and `reset_execution` bind runtime identity at every ingress. Serialized durable context must be revalidated after deserialization.
- `execution_guard` fences policy checks and work registration against concurrent mutation. Release the fence before long-running work; blocking admission/teardown cannot block the async event loop.
- Schedules/jobs persist owner and execution context; updates cannot replace that identity. Recovery does not silently resume privileged work.

## Revocation and quotas

- Policy mutations increment generations and set persistent `access_pending` barriers before teardown. Missing/failing stop acknowledgements retain the barrier; retries do not waive authorization.
- Startup registers composite stoppers for tasks, containers, terminals, jobs and remote transports. Shutdown stops ingress and confirms teardown; no backend failure silently falls back to host execution.
- Quotas include CPU, RAM, disk, duration, concurrency and GPU policy. A shared ledger attributes work to owners across supported backends; private writes use reservations and server control-plane caps.
- Filesystem accounting is not a substitute for hard per-user persistent disk quotas. Provision bounded storage and verify the actual deployment boundary.

## Local and remote backends

- Restricted local tools run in a nonroot per-chat container with only authorized RO/RW mounts, private PID/IPC, read-only image, dropped capabilities, no runtime socket or coordinator data, bounded scratch and network disabled by default.
- Persistent project files are separate from disposable environments. Held terminals/jobs retain their supervised environment; ordinary unheld actions remove it to kill descendants.
- Unrestricted host work is explicitly authorized, supervised and quota-admitted, but is not OS isolation. Effects permitted to the host account remain a documented caveat.
- Restricted remote execution must provide an equivalent destination boundary. The current SSH host-command path and connector marker attestation do not prove that boundary; see the release blockers before enabling either for Members.
- Cross-machine resources are explicit transfer endpoints. Transfers require source read and destination write scope, bounded streaming/staging, cleanup and revalidation.

## Tools, state and channels

- Member MCP/plugin invocation uses scoped adapters; shared management stays Admin-only. Tool schema visibility alone is not invocation authorization.
- Personal skill pins/agent state are separate from shared agent configuration. Members cannot mutate global agent profiles or inherit server credentials.
- Browser rendering runs in the local sandbox; online input is fetched through scoped policy and rendered offline. This is not general live-site browser automation.
- Linked Telegram accounts use ordinary owner contexts. Allowlisted legacy unlinked `tg_*` chats use explicit trusted Admin handling only; never expose that exception to untrusted/public groups.
- Audit entries contain fixed identity/resource/outcome metadata, not arbitrary contents or secrets.

These interfaces describe intended integration behavior. Passing tests of a protocol or admission seam do not establish all OS/deployment enforcement; the status and verification documents explicitly track those gaps.
