---
title: Multi-user roles, shared workplaces, and isolated execution
status: superseded
created: 2026-10-04
---

# Spec: User roles, workplace sharing, and Tomo sandboxes

> Superseded by the approved design in [the Superpowers specification](../superpowers/specs/2026-10-04-multi-user-workplace-access-design.md). Retained as the exploratory draft; its proposed defaults and open questions are not the current design.

## 1. Goal

Allow multiple users to use Tomo through a shared coordinator without giving each user access to the entire machine, credentials, or other users' data. Users must still be able to run the full toolchain, select working folders, use tunnels, and work across shared folders.

This document specifies behavior and security boundaries, not an implementation plan. It is a **draft**: decisions from the discussion are separated from proposed defaults that have not yet been approved.

## 2. Decision basis

### Agreed in the discussion

- The coordinator remains the default entry point; a separate coordinator profile per user is unnecessary.
- Users can select a folder/workplace as their working location.
- Active folder A does not restrict access to A alone. Authorized folder B, when enabled in the chat, can also be used without changing the active folder.
- Sharing does not allow every user to select every host folder.
- Agents can be directed to tunnel workplaces; a tunnel itself is not a sandbox.
- The tools in `Dockerfile.full` define the baseline execution environment requirements, including FFmpeg and the document/browser toolchain.
- Roles alone cannot secure terminal access; resource authorization and execution isolation are also required.

### Proposed defaults for the first release

- Two roles: **Admin** and **Member**. Builder/Viewer/Owner are not introduced yet.
- Members are not automatically trusted with host access.
- New chats receive persistent personal storage without manual folder setup.
- Shared folders have **read-only** or **read-write** permissions.
- Sharing recipients cannot reshare by default.
- A full-toolchain container is a candidate baseline for local sandboxes; Bubblewrap remains an alternative. The backend choice is not final.
- No silent fallback to the host when a sandbox/tunnel is unavailable.

## 3. Current code behavior

Source review found a foundation for user identity, but not yet a multi-user execution boundary:

| Area | Current behavior | Implication |
|---|---|---|
| Sessions | API paths check session ownership; client-supplied identity is not trusted | Preserve these ownership checks |
| Runtime | `run_turn` binds the session owner for user/memory context | Use this identity for tool/resource authorization as well |
| Tools | Tools come from agent configuration | Intersect them with user/chat permissions |
| Delegation | The delegation roster includes all enabled agents | Globally enabled does not mean authorized for a user |
| Workplaces | Session selection validates workplace existence | Existence does not establish access rights |
| Local execution | Bash uses subprocess and cwd; the default work directory is agent-scoped | This is neither an OS sandbox nor private per-user storage |
| Approvals | Action approvals and outside-path grants | These must not be treated as resource authorization |
| Platform API | Many administrative endpoints require authentication only | Enforce server-side restrictions before allowing member use |

References: `app/core/deps.py`, `app/api/rest.py`, `app/api/platform.py`, `app/services/store.py`, `app/channels/web.py`, `app/runtime/agent/loop.py`, `app/runtime/tools/sandbox.py`, `app/runtime/tools/workplace_remote.py`, `app/runtime/permissions/gate.py`.

These findings are based on source inspection, not exploit testing or a complete security audit.

## 4. Access model

Separate four concepts:

1. **Role**: which platform actions a user may perform.
2. **Assignment/sharing**: which agents, models, workplaces, and credentials a user may use.
3. **Ownership**: who owns data/resources and who may manage them.
4. **Execution isolation**: what a process can reach on the target machine.

### Core objects

- **User**: the account that owns the session and performs actions.
- **Agent profile**: model configuration, instructions, skills, and capabilities; it may be shared.
- **Session/chat**: a user-owned conversation, active folder, and additional access list.
- **Workplace**: a local working resource, managed project, or remote destination through tunnel/SSH.
- **Folder grant**: a user's permission for a folder/workplace; at minimum, read or write.
- **Execution context**: user/session identity, executing agent, enabled resources, and effective permissions for a job.

A shared agent profile does not merge users' storage, credentials, or permissions. An agent's `is_super` flag is not a user's Admin role.

### Authorization invariant

Effective permissions = user permissions ∩ agent capabilities ∩ resources enabled in the chat ∩ execution backend restrictions.

- Selecting/enabling a resource in a chat does not create new access rights.
- Agent names, prompts, workplace hints, or model responses cannot grant permissions.
- Approval authorizes an action **within** existing permissions only.
- Authorization occurs before tool execution, not just through tool schemas or UI menus.
- Direct ID/path requests, tools, delegation, swarms, interactive terminals, API keys, schedulers, and background jobs must honor the same boundary.
- API keys inherit their owner's account restrictions; they are not an admin bypass.
- Missing valid identity/permissions must fail closed, not use a privileged/default identity.

## 5. Roles and platform permissions

| Action | Admin | Member |
|---|---|---|
| Chat and manage personal sessions | Yes | Yes |
| Use assigned agents/models | Yes | Yes |
| Manage files in assigned workplaces | Subject to access rights | Subject to access rights |
| Run terminal commands | Host or configured sandbox | Authorized isolated/restricted destinations only |
| Create personal managed projects | Yes | Yes, proposed default |
| Register arbitrary host paths or connectors/tunnels | Yes | No by default |
| Assign agent/model/workplace access | Yes | No |
| Manage providers, server secrets, plugins, global tools, and shared agents | Yes | No |
| Manage accounts and roles | Yes | No |
| Manage personal credentials/API keys | Yes | Own resources only |
| Update own profile/password | Yes | Yes |
| Access other users' private data | Not automatically through chat | No |

Admin is an instance administrator, not a confidentiality guarantee against the machine operator: an operator who controls the host can still access storage. Explicit administrative access to user data requires a separate policy and audit trail; an admin coordinator must not automatically mix all users' data.

Builder is deferred until agent/automation ownership and configuration sharing exist. If introduced, Builder must not expand tool, workplace, or credential access beyond its assignments.

## 6. Working location and additional access UX

### F1 — New chat

1. The user opens a chat with the default coordinator.
2. The default working location is **Personal space**.
3. Uploaded files and work outputs persist for their owner.
4. Users are not required to understand containers, mounts, or server paths.

Proposal: personal space is user-scoped and reusable across chats; managed projects separate files for specific tasks. Container/process boundaries do not have to match persistent storage boundaries.

### F2 — Select a working location

The picker shows authorized resources only:

```text
Working location: Video campaign

Personal space
Personal projects
Shared with you
Connected machines / remote workplaces

+ Create project
```

- The active folder is the default cwd/destination for commands that do not specify a workplace.
- The UI shows the workplace name, read/write permission, target machine, and connection status.
- Changing the active folder changes the destination for subsequent work; it does not move existing files or processes.
- Members do not receive an unrestricted host filesystem picker.
- Admins can connect host folders and grant access to specific users.

### F3 — Work across folders

```text
Working location: A

Additional access:
[x] B           Read-write
[x] Reference   Read-only
[ ] C           Read-write
```

- A is the default, not the only visible folder.
- B and Reference are available because the user has grants and has enabled them in the chat.
- The agent cannot use C until the user enables it.
- A request such as "take the video from B and save the result in A" does not require changing the active folder.
- The agent may suggest enabling an authorized resource, but cannot add access unilaterally.
- Proposed default: new chats enable only the active folder; users select additional resources.

### F4 — Sharing

- The owner/admin selects recipients and read or write permission.
- Read permits reading and, where available, running tools to produce output in another writable folder; it does not permit modifying the source.
- Write includes creating, modifying, and deleting files. Destructive-action approval may still apply.
- Recipients cannot change grants or reshare by default.
- Unauthorized folders do not appear in pickers, autocomplete, rosters/context, or tool listings.
- Unauthorized links do not disclose resource metadata. "Request access" is available only for invitations/links permitted to reveal that resource.
- Sharing a folder does not automatically share chats, memory, server secrets, or agent configuration.

## 7. Coordinator, delegation, and asynchronous work

The shared coordinator receives the session owner's execution context, not the coordinator profile's entire privilege set.

- Unauthorized tools/agents/models are not offered and remain blocked if called directly.
- Delegation targets only agents the user is authorized to use.
- Subagents/swarms inherit the parent's access restrictions; specialist capabilities do not grant additional permissions.
- Agents cannot create agents, register workplaces, change settings, or use plugins to bypass role restrictions.
- Background jobs and schedules retain the owner's identity and explicit target resources.
- Permissions are revalidated when jobs execute/resume; jobs cannot use another user's credentials/resources.
- Jobs that cannot perform interactive approval do not automatically receive additional privileges.
- Rosters, prompts, error outputs, and tool metadata must not disclose forbidden resources.

## 8. Local sandboxes and the full toolchain

### Persistence

- Personal-space/project files persist.
- Processes/containers may be temporary and stopped when idle.
- A new sandbox can reuse the same storage without access to other users' storage.
- Stopping a sandbox does not automatically delete a project. Storage deletion is a separate action.

### Tool environment

The baseline follows the requirements in `Dockerfile.full`:

- FFmpeg, ImageMagick, yt-dlp.
- LibreOffice, Poppler, Pandoc, Tesseract with English/Indonesian support.
- Python and PDF/Word/Excel/PowerPoint libraries, pandas, matplotlib.
- Node.js/npm, uv, build tools, git/SSH, ripgrep, jq, sqlite3, archive utilities.
- CloakBrowser/Chromium, fonts, and browser dependencies.

Binaries/runtimes may be shared read-only. Writable caches/home directories must have a defined scope, rather than an unrestricted shared server home. Skill support scripts may be available read-only without exposing live server configuration.

The current full image is a coordinator application image. Its toolchain can provide the baseline, but the server CMD, database, `TOMO_HOME`, and server credentials are not part of the member environment.

### Required boundaries

- Mount only the authorized active folder and additional resources, with their respective read/write permissions.
- Do not mount the entire host filesystem, Tomo application data, Docker/container runtime sockets, or host control sockets.
- Do not inherit the server's entire environment/credential set.
- Prevent escapes through symlinks, mount aliases, path traversal, and host process access.
- Enforce read-only access at the backend/filesystem level, not just in file tools; it also applies to bash/Python/browser execution.
- Restrict network access and Tomo control endpoints according to user identity; terminals must not provide access to privileged APIs.
- Apply CPU, RAM, disk, and duration limits. There is no per-user concurrency cap. GPU access is optional and explicitly granted.
- Tools or MCP services running outside the sandbox still require equivalent resource authorization.
- If the isolation backend is unavailable, member terminal execution fails closed. It must not fall back to host bash.

## 9. Tunnels and cross-machine work

- Users can use tunnel workplaces assigned to them.
- Tomo still checks user permissions; the connector enforces restrictions on the target machine.
- A connector with broad host access is not automatically safe because it uses a tunnel.
- For members, remote destinations must meet the required OS-account/sandbox and filesystem boundaries. Unrestricted destinations are reserved for explicit trusted/admin access.
- The full toolchain must be available at the execution destination; installing it on the server does not automatically provide FFmpeg on the connector.
- A and B on the same machine may be visible in the same sandbox if both are authorized.
- A and B on different machines require an authorized transfer: check source read and destination write permission, log the transfer, and do not mount entire machines or use implicit credentials.
- An offline tunnel returns an error identifying the destination; it does not fall back to the server or another machine.

## 10. Access changes, concurrency, and audit

### Enable/disable and revocation

- Check current access rights before new actions.
- Disabling additional access applies to subsequent work; the UI warns if active processes still use the resource.
- Security proposal: stop affected work and update the sandbox/mounts before subsequent execution. Removing a resource from the prompt is insufficient.
- An owner/admin revoking a grant must terminate active access, not merely hide it in the picker. Revocation mechanisms/latency are criteria for choosing the backend.
- Files already copied elsewhere and data already present in chat are not automatically recalled. Read sharing allows recipients to copy data; the UI does not promise DRM.

### Concurrency

- Chats accessing the same folder see the same filesystem changes.
- The UI shows relevant active work without disclosing other parties' private sessions.
- Automatic merging or conflict-free editing is not promised. File conflicts/changes must not be concealed as successful operations.

### Audit

At minimum, record the user, session/job, executing agent, tool/action, target workplace/machine, outcome, and grant changes. Avoid recording secrets or entire file contents in audit logs.

## 11. Acceptance examples

1. **AE1 — Private defaults:** two members use the same coordinator but cannot see each other's files/memory/sessions; the default work directory is not scoped to the shared coordinator.
2. **AE2 — A to B:** A is active, and B is shared read-write and enabled. The agent reads B and writes results to A without changing the active folder.
3. **AE3 — B not enabled:** the user has access to B, but B is not enabled. The tool is denied or suggests enabling it, rather than opening B automatically.
4. **AE4 — Another user's folder:** another user's workplace ID/path is submitted directly through an API, hint, or tool. Access is denied without leaking metadata/files.
5. **AE5 — Enforced read-only:** editing/deleting through tools, bash, Python, or symlinks in a read-only folder fails; new output may be created in writable A.
6. **AE6 — No delegation escalation:** a specialist has broader global access, but a member's task can use only the parent's authorized resources.
7. **AE7 — No approval escalation:** once/always/auto approval does not permit reading other users' data or host secrets.
8. **AE8 — Administrative platform access:** members cannot modify other accounts, providers, plugins, shared agents, grants, or settings through APIs or tools.
9. **AE9 — Full tools:** a member can run FFmpeg and produce output in their own workspace without seeing Tomo's database or server credentials.
10. **AE10 — Offline tunnel:** a command targeting a remote destination fails with a destination-specific error and does not run locally.
11. **AE11 — Cross-machine work:** transferring remote B to local A requires read B + write A; it does not grant other access to either machine.
12. **AE12 — Revocation:** a grant is revoked while a job runs; active access is terminated, subsequent commands are denied, and pending jobs fail closed.
13. **AE13 — Persistent files:** an idle sandbox is stopped and reopened; project files remain, and other users' storage remains invisible.
14. **AE14 — API keys:** a member's API key cannot create keys for other accounts or perform admin actions.
15. **AE15 — No unsafe fallback:** failures in policy lookup, identity, sandbox, or resolution do not downgrade execution to a privileged default/host environment.

## 12. First-release non-goals

- Organization hierarchies, billing, custom roles, or a generic policy editor.
- Builder/Viewer/Owner before requirements and resource ownership are clear.
- Creating one coordinator/profile per user.
- Allowing members to select arbitrary host paths.
- Protecting data from a machine administrator who controls the host.
- DRM for files already read/copied, automatic merging, or comprehensive collaborative filesystem locking.
- System/root package installation by members. User-space packages must still follow network, storage, and quota policies.

## 13. Open questions before implementation planning

1. **Isolation backend:** full-toolchain containers, Bubblewrap, or a combination; how does a Tomo deployment that itself runs in Docker operate the backend without exposing its socket to members?
2. **Environment lifetime:** sandbox per user, project, or session? Persistent disk and the default folder are separate decisions; define the scope of home/cache and user-space packages.
3. **Sharing policy:** can members directly share personal managed projects, or is sharing admin-only in the first release? External host/tunnel resources remain admin-controlled by default.
4. **Network/secrets:** open networking with restricted control endpoints, or an allowlist? Which credentials can be assigned, and how does the broker prevent cross-user privilege escalation?
5. **Revocation:** the time limit for terminating active access and required connector/job-management support.
6. **Quotas:** CPU/RAM/disk/duration/concurrency values, workspace retention, and audit retention.
7. **Admin support:** when can admins access user data/sessions for support, and how are consent/audit details presented?

Proposed defaults may be revised during review. The implementation must not be labeled safe for untrusted multi-user use merely because pickers/roles exist; all execution entry points and isolation boundaries must satisfy the acceptance examples.
