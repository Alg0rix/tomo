# Multi-user implementation status

Approved design: [multi-user workplace access](../superpowers/specs/2026-10-04-multi-user-workplace-access-design.md).

This change is **draft work, not approved for deployment to untrusted Members**.
Passing functional tests do not establish the complete containment contract.

## Implemented and locally exercised

- Current-account authentication, Admin/Member roles, ownership, resource/model/agent grants and private API keys.
- Personal/project resources, RO/RW sharing, per-chat activation and delegation ceilings.
- Local Docker chat environments, supervised jobs/terminals, persistent authorized mounts and revocation barriers.
- Telegram account linking/unlinking, linked-account contexts, account/sharing/admin UI and owned upload destinations.
- Shared admission ledger, request limits before parsing, private-write reservations and storage capability checks.
- Member MCP/plugin sandbox adapters, personal skill activation/state, offline and scoped fetch-then-render browser paths.
- Remote execution-context protocol, scoped transfer plumbing and SSH destination-agent transport.

## Open acceptance and release blockers

- **Restricted SSH is not equivalent to a per-chat container.** `app/workplaces/ssh_agent.py` advertises a sandbox from a marker, but `op_exec` launches host Bash with a cwd and duration bound. This does not enforce filesystem namespaces, RO mounts or CPU/RAM quotas. Do not enable it for untrusted Members.
- **Remote tunnel sandbox attestation is insufficient.** `connector/internal/executor/sandbox.go` compares an operator marker with an image value; this alone does not prove per-chat environments, authorized mount sets, resource limits or revocation containment. Real destination isolation needs implementation/verification beyond a marker.
- **Persistent disk fairness is not proven.** Private-write accounting and shared filesystem capacity checks are not per-user kernel-backed disk quotas. Bounded/per-user storage must be provisioned and verified on the deployment host.
- Scoped browser retrieval/rendering is not general live-site browser automation. External-origin retrieval and live SSH rollout were not exercised.
- Full swarm/channel acceptance, sustained concurrent quota/revocation stress and independent security reviews remain incomplete. Earlier provider-rejected reviews did not pass.

## Verification

Latest pre-cleanup run: **1,256 Python tests passed**, **127 scoped multi-user tests passed**, **17 real Docker isolation tests passed**, Go connector tests passed, **12 Node tests passed**, and headless Member UI flows passed. Python reported 1 existing Pydantic warning. The Docker wrapper had a transient core-dump before a clean rerun; do not treat it as proof of sustained runner stability.

See [verification and reproduction](2026-10-04-multi-user-integration-evidence.md), [implementation contracts](2026-10-04-multi-user-implementation.md) and [deployment requirements](../../deployment/sandbox/README.md).

Generated logs, screenshots, local probes and BB workflows are excluded from source control. Historical artifacts are retained outside the checkout; committed tests and documentation are the reproducible review surface.
