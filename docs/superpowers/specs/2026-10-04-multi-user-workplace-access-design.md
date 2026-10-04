---
title: Multi-user roles, shared workplaces, and isolated execution
status: design-approved
created: 2026-10-04
workflow: superpowers-brainstorming
---

# Multi-user workplace access design

## Goal and approved scope

Multiple users share Tomo's default coordinator while keeping their data, credentials, and execution permissions separate. Members can use the full toolchain, select working folders, enable additional shared folders, and execute through tunnels.

Members are **untrusted by default**. An Admin can separately grant folder access and unrestricted execution on a specific machine/workplace. An unrestricted grant deliberately removes the sandbox guarantee for that destination; it is not a general elevation of the user's platform role.

The user approved the architecture, sharing/lifecycle behavior, and error-handling/security sections through the Superpowers brainstorming process. This document awaits final written-spec review. It supersedes the exploratory draft in `docs/specs/2026-10-04-multi-user-workplace-access.md`.

## Approach selection

Three approaches were considered:

| Approach | Strength | Trade-off |
|---|---|---|
| Container per chat | Separates chat processes/environments and makes chat-specific resource access explicit | More container lifecycle work; shared project storage still needs authorization |
| Container per user | Reuses packages/environment with fewer containers | Chats with different access sets share processes/environment |
| Bubblewrap per execution | Lightweight and reuses a local toolchain | Requires additional process, quota, browser, and revocation management |

**Selected:** container per chat for restricted local execution, with persistent user/project storage. Container backend configuration remains security-critical; using a container alone does not establish isolation. Remote execution must meet equivalent boundaries on the destination machine.

## 1. Domain and permissions

### Roles

| Capability | Admin | Member |
|---|---|---|
| Chat and manage own sessions | Yes | Yes |
| Use authorized agents/models | Yes | Yes |
| Create personal managed projects | Yes | Yes |
| Share owned managed projects | Yes | Yes |
| Use shared folders | Subject to grants | Subject to grants |
| Register external host folders, connectors, and tunnels | Yes | No |
| Assign agent/model/workplace access | Yes | No |
| Grant unrestricted execution | Yes | No |
| Manage accounts, roles, providers, server secrets, plugins, global tools, and shared agent configuration | Yes | No |
| Manage own profile/password and personal API keys | Yes | Yes |
| Access another user's private chat data automatically | No | No |

Builder, Viewer, Owner, organizations, and custom roles are outside this release.

### Objects

- **User:** authenticated account and owner of sessions/jobs.
- **Agent profile:** reusable model, instructions, skills, and capabilities. Shared profiles do not share user data or permissions.
- **Chat:** conversation owner, active workplace, enabled additional resources, and selected execution mode.
- **Managed project:** persistent user-owned storage that can be shared.
- **Workplace:** local managed storage, an externally registered folder, or a remote destination.
- **Resource grant:** read or read-write access to a workplace/project.
- **Unrestricted execution grant:** Admin-controlled permission for a particular user on a specific destination. Separate from folder access.
- **Execution context:** user/session identity, executing agent, authorized enabled resources, execution mode, and target destination.

### Invariants

For restricted execution:

**Effective access = user permissions ∩ agent capabilities ∩ resources enabled in the chat ∩ backend restrictions.**

- A grant permits use; enabling a resource in a chat activates that existing permission.
- Folder selection, prompts, tool arguments, model output, and approval responses cannot create grants.
- `is_super` on a coordinator is not the user's Admin role.
- Every action is authorized server-side before execution, including calls not offered by the UI/tool schema.
- API keys inherit their account's permissions. Members may manage only their own keys.
- Missing identity, failed policy lookup, or unavailable isolation fails closed.
- Unrestricted execution bypasses sandbox resource containment only for the granted destination. Platform API authorization and account ownership checks remain in force.

An unrestricted process can reach anything its OS account can reach, potentially including other users' data or credentials. Tomo cannot promise cross-user confidentiality on that machine. This exception is a conscious trust decision, not a sandbox equivalent.

## 2. Architecture and data flow

```text
User
  -> owned chat
    -> shared coordinator
      -> effective authorization check
        -> restricted chat container
        -> remote execution boundary
        -> explicitly enabled, Admin-granted unrestricted destination
```

### Responsibility boundaries

| Unit | Responsibility | Inputs/dependencies |
|---|---|---|
| Authorization | Validate role, ownership, grants, chat activation, and execution mode | Authenticated identity, account/resource state |
| Storage and sharing | Own persistent projects and administer folder grants | User/project ownership and grant policy |
| Sandbox lifecycle | Provision chat containers, mounts, quotas, and termination | Authorized execution context and persistent storage |
| Local/remote routing | Select an authorized destination and dispatch work | Execution context, connector status |
| Audit | Record actions and permission changes without recording secrets | Validated actor, destination, outcome |

The coordinator proposes actions; it is not the authority that grants access.

### Turn execution

1. Authenticate the caller and verify session ownership.
2. Resolve permitted agents/models and the chat's active/additional workplaces.
3. Validate current grants and execution mode before each tool action.
4. Dispatch to the chat container or authorized remote destination.
5. Record destination and outcome; return results to the owning chat.

The same checks apply to interactive terminals, delegated tasks, swarms, MCP/tools outside containers, schedules, background jobs, resumed jobs, and API-key requests.

### Delegation and asynchronous work

- Delegation targets only agents the user may use.
- Subagents inherit the parent's effective access ceiling. Specialist capabilities cannot widen access or enable unrestricted mode.
- Creating agents, registering workplaces, changing settings, and invoking plugins cannot bypass role policy.
- Jobs retain owner identity, explicit destination, and resource scope. Recheck current permission before starting/resuming actions.
- Lack of interactive approval does not grant extra privileges.
- Prompts, rosters, listings, and errors must not reveal unauthorized resources.

## 3. Working-location UX

### Default

A new chat uses **Personal space** with persistent user-scoped files. Users need not configure mounts or understand containers. Personal storage may be reused across chats; managed projects separate work by task.

### Picker

```text
Working location: Video campaign

Personal space
Personal projects
Shared with you
Connected machines / remote workplaces

+ Create project
```

Show only authorized resources, together with permission, target machine, and connection state. Members cannot browse arbitrary server paths. Admins register external host folders/tunnels and assign access.

The active folder is the default cwd/destination for actions without an explicit workplace. Changing it affects subsequent work; it does not move files or existing processes.

### Additional access

```text
Working location: A

Additional access:
[x] B           Read-write
[x] Reference   Read-only
[ ] C           Read-write
```

A is the default, not the only permitted folder. Enabled B can supply input while output is saved in A. C remains unavailable until the user enables it, even if the user holds a grant. Agents may suggest enabling authorized resources but cannot activate them unilaterally.

New chats enable only their active folder. User-selected additional access is persisted with the chat and revalidated against current grants.

### Unrestricted mode

Only an Admin can issue a destination-specific grant. The user must explicitly activate unrestricted execution in the chat, with a warning that host access follows the OS account and cross-user isolation is not guaranteed. Keep the mode and destination visible. Never switch into unrestricted execution implicitly.

A grant for one workplace does not authorize unrelated destinations on the same or another machine. However, once execution is unrestricted at the granted destination, filesystem access is governed by the OS account rather than folder mounts; the UI must not imply otherwise.

## 4. Sharing and lifecycle

- Members can share their own managed projects; external host/tunnel resources remain Admin-controlled.
- The owner/Admin selects recipients and **read-only** or **read-write** permission.
- Read-only permits reading/copying input, not modifying the source. Outputs go to another writable resource.
- Read-write includes creating, editing, and deleting files; destructive-action approval may still apply.
- Recipients cannot reshare or alter grants by default.
- Sharing a folder does not share chats, memories, agent configuration, or credentials.
- Unauthorized resources stay out of pickers, autocomplete, tool listings, and context. Unauthorized links do not disclose metadata. Request-access UI is limited to invitations/links permitted to reveal that resource.

### Persistence and idle shutdown

Each restricted chat has its own container environment. Containers can stop when idle; personal/project files persist independently. Reopening creates/restores an environment using current authorized mounts, not stale permissions. Deleting persistent storage is a separate action.

Writable home/cache and user-installed packages belong to the chat environment, not the server home. Their survival across environment recreation is not promised; project files are the persistence guarantee.

### Access changes

- Check current grants before new actions.
- Disabling a resource, changing permissions, or revoking a grant stops affected managed work and removes/updates mounts before subsequent execution. Removing a prompt/tool entry is insufficient.
- Rebuild/restart affected environments if needed to remove retained filesystem access. Queued work uses current permissions, not a cached grant.
- Mark affected access unavailable while teardown is pending; do not report completed revocation while managed processes retain access.
- Revoking unrestricted access stops Tomo-managed processes and prevents new execution. Tomo cannot guarantee rollback of host effects or termination of processes that escaped supervision.
- Files already copied and content already included in chats are not recalled. Read access is not DRM.

### Concurrent shared work

Chats accessing the same project see the same filesystem changes. Show relevant active work without revealing another user's private conversations. No automatic merge or conflict-free editing guarantee is made. Detected conflicts or concurrent modifications must not be reported as successful edits.

## 5. Execution environment and tunnels

### Full toolchain

Use the toolchain requirements in `Dockerfile.full` as the sandbox baseline:

- FFmpeg, ImageMagick, yt-dlp.
- LibreOffice, Poppler, Pandoc, Tesseract English/Indonesian.
- Python, document/PDF libraries, pandas, matplotlib.
- Node.js/npm, uv, build tools, git/SSH, ripgrep, jq, sqlite3, archive utilities.
- CloakBrowser/Chromium, browser dependencies, fonts.

Do not use the coordinator's runtime data as the sandbox environment. Server startup, database, live `TOMO_HOME`, server credentials, and control sockets must not be exposed. Skill support scripts may be supplied read-only.

### Restricted execution requirements

- Mount active/additional authorized folders with actual read-only/read-write enforcement.
- Do not expose the entire host, other users' storage, host processes, container runtime socket, or privileged control sockets.
- Prevent path traversal, symlink, and mount-alias escapes.
- Apply the boundary to bash, Python, browsers, file tools, and external tool services alike.
- Do not inherit server secrets/environment. Supply only explicitly authorized credentials.
- Internet access may be available for tools, but unauthorized internal/control endpoints must be restricted. Network reachability must not turn terminals into a privileged API path.
- Apply per-user aggregate CPU, RAM, disk, duration, and job/concurrency limits. Admins configure values; roles do not encode quota numbers. GPU use requires explicit assignment.
- If the backend cannot enforce required isolation, reject restricted execution; never downgrade to host bash.

### Remote destinations

A tunnel transports requests; it does not isolate them. Connectors must enforce equivalent account/sandbox/filesystem boundaries on the destination machine for restricted work. Broad host connectors require explicit unrestricted grants.

The toolchain must exist at the execution destination. Server-side FFmpeg installation does not supply remote FFmpeg automatically.

Authorized A/B on the same machine can be exposed to the same restricted container. Across machines, transfer is explicit: authorize source read and destination write, record the transfer, and use scoped credentials. Do not transparently mount entire machines or silently route to another machine.

## 6. Errors and audit

- Invalid identity/permission, failed policy resolution, unavailable sandbox, or offline tunnel rejects execution with a safe, destination-specific error.
- Never silently fallback to the host, another machine, another user, or unrestricted mode.
- Approval once/always/auto applies only within the access ceiling; it cannot permit another user's files or host secrets.
- Record actor, session/job, executing agent, tool/action, destination, outcome, and grant/mode changes.
- Do not log secrets or entire file contents. Operator host access is outside the confidentiality guarantee; Admin status alone does not automatically expose private chats through the application.

## 7. Verification and acceptance

Verification must exercise real authorization and execution boundaries, not only hidden menus or model compliance.

1. Two members using the same coordinator cannot read each other's private files, memory, or sessions through APIs, file tools, bash/Python, terminals, or delegation.
2. Direct workplace IDs/paths, hints, and tool calls cannot access resources outside the user's grants or the chat's enabled set.
3. With A active and B enabled, FFmpeg can read B and write output to writable A without switching folders.
4. Read-only B rejects writes/deletes through file tools, shell/Python, and symlink paths.
5. A disabled B remains inaccessible until the user enables it; the agent cannot activate it itself.
6. Members can share owned managed projects with read/write permissions, but cannot register arbitrary host folders or reshare received projects.
7. Delegation, swarms, background jobs, schedules, resumes, and external tools preserve the parent's authorization ceiling and revalidate grants.
8. Approval choices and unattended jobs do not widen permissions.
9. Member APIs/keys/tools cannot modify other accounts, issue keys for others, manage server providers/secrets/plugins, or change shared agent configuration.
10. Restricted environments run the full toolchain without exposing Tomo's database, credentials, runtime sockets, or other users' storage.
11. Unrestricted execution requires both a matching Admin grant and explicit chat activation; other destinations and platform admin actions remain unauthorized.
12. Offline tunnels and backend/policy failures never execute locally or under a privileged default.
13. Cross-machine transfers require source read plus destination write and scoped credentials.
14. Revocation stops affected managed execution, removes retained access, and denies queued/new work. Unrestricted supervision limits are stated, not hidden.
15. Idle shutdown/recreation preserves project files while revalidating mounts and keeping users' storage separate.
16. Aggregate quotas and audit records remain attributed to the owning user, including delegated/async work.

## 8. Existing-code integration context

Source inspection identified useful identity foundations and missing boundaries:

| Area | Existing reference | Design implication |
|---|---|---|
| Auth and session ownership | `app/core/deps.py`, `app/api/rest.py` | Preserve authenticated ownership; add role/resource checks |
| Administrative endpoints | `app/api/platform.py` | Authentication alone is insufficient |
| Shared coordinator creation | `app/services/store.py` | Retain profile reuse without reusing access privileges |
| Delegation and workplace binding | `app/channels/web.py` | Filter targets/resources by execution context |
| User context and tool schemas | `app/runtime/agent/loop.py`, `app/runtime/tools/user_ctx.py` | Extend owner identity into enforced authorization |
| Local work roots and bash | `app/runtime/tools/sandbox.py`, `app/runtime/tools/bash.py` | Agent-scoped cwd is not per-user isolation |
| Remote resolution | `app/runtime/tools/workplace_remote.py` | Authorize overrides/hints before dispatch |
| Approval grants | `app/runtime/permissions/gate.py` | Keep approvals below role/resource boundaries |
| Toolchain | `Dockerfile.full`, `Dockerfile` | Separate execution environment from coordinator data/runtime |

These are source-review observations, not a completed exploit test or security audit.

## 9. Delivery boundary

This is one integrated access-control design, with implementation work sequenced by dependency:

1. Account roles, ownership, resource grants, and a common authorization contract.
2. Chat access UX and checks on APIs, coordinator tools, delegation, and jobs.
3. Restricted local container lifecycle and full-toolchain storage/mount enforcement.
4. Equivalent remote enforcement, cross-machine transfer, and explicit unrestricted mode.
5. Revocation, quotas, audit, and end-to-end verification.

Do not release this as safe for untrusted members after only adding roles/pickers. Keep affected member execution unavailable until its full boundary is enforced. The implementation plan must select the concrete container runtime, deployment/broker topology, network enforcement, connector integration, and revocation mechanism without weakening this design. Those engineering choices are not permission-policy placeholders.

### Non-goals

- Custom roles, organization hierarchy, billing, or a generic policy editor.
- Separate coordinator profiles per user.
- Unrestricted filesystem picking by Members.
- Confidentiality against host operators or on explicitly unrestricted destinations.
- DRM, rollback of unrestricted host effects, automatic file merging, or comprehensive collaborative locking.
- Member system/root package installation; user-space installation remains subject to network/storage/quotas.
- Guaranteeing package/cache persistence beyond project files.

## Spec self-review

- No unresolved product-policy placeholders remain; backend engineering choices belong in the implementation plan.
- Restricted containment and explicitly unrestricted execution are separate, including their revocation guarantees.
- Folder grants, chat activation, delegation, persistent storage, and remote transfers use the same authorization model.
- Scope is limited to the agreed Admin/Member and workplace-execution boundary; unrelated refactoring and extra roles are excluded.
