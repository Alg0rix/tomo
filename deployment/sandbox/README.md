# Sandbox deployment prerequisites

**Draft implementation: do not deploy to untrusted Members yet.** Read the
[status and blockers](../../docs/plans/2026-10-04-multi-user-completion.md) and
[verification scope](../../docs/plans/2026-10-04-multi-user-integration-evidence.md).
Local Docker checks passed. Remote containment, production storage fairness and
complete security/acceptance review have not been established.

## Build the local full-toolchain sandbox

`Dockerfile.sandbox` copies the toolchain from `Dockerfile.full` into a clean
scratch image. It must not inherit live coordinator state, credentials, DB,
TOMO_HOME, runtime sockets or server home. Never build from an exported running
coordinator filesystem containing secrets.

```sh
docker build -t tomo:local .
docker build -f Dockerfile.full --build-arg TOMO_BASE_IMAGE=tomo:local -t tomo:local-full .
docker build -f Dockerfile.sandbox --build-arg TOMO_FULL_IMAGE=tomo:local-full -t tomo:sandbox .
```

Use a dedicated non-root coordinator account with access to a trusted local
Docker daemon (Docker >=25 for `bind-recursive=disabled`). Docker socket access
is operator authority; never mount it into a chat or member-accessible folder.
Rootless Podman is implemented but not exercised by the local verification.
It requires CPU/memory/PID cgroup controllers and keep-id support.

```dotenv
TOMO_SANDBOX_RUNTIME=docker
TOMO_SANDBOX_IMAGE=tomo:sandbox
TOMO_SANDBOX_NAMESPACE=tomo-production
```

The backend pins the image ID. Managed storage paths must refer to the same
persistent files on the runtime host. A containerized coordinator needs a
trusted broker deployment with consistent paths; no generic HTTP broker or
remote-daemon path translation is established here.

## Local boundary and lifecycle

Restricted local execution uses nonroot per-chat containers, authorized RO/RW
resource mounts, private PID/IPC, default seccomp, cap-drop ALL,
no-new-privileges, read-only images and bounded writable home/tmp/shm. No host
home, DB, server credentials, devices or runtime socket is exposed. Resource
paths inside the container are `/workplaces/<resource id>`, not host paths.

Unheld action environments are removed to terminate descendants. Terminals and
background jobs hold supervised environments; completing one action must not
kill unrelated held work. Persistent project files survive environment removal.
Idle/duration limits and revocation teardown remain required for retained work.
Startup removes namespace orphans before admission. Backend failures deny
restricted execution rather than selecting host execution.

The shared ledger admits supported turns, containers, host processes, jobs and
terminals against owner quotas. Containers enforce CPU/RAM/PID/scratch limits.
Explicitly unrestricted host work is not OS isolation: `prlimit`, duration and
process supervision do not turn a host account into a security boundary or
provide every cgroup-style aggregate limit.

## Network and browser behavior

Chat containers retain `network=none`; there is no broad bridge/host-network
fallback. Scoped web retrieval and browser input use coordinator policy/broker
paths. Browser input is fetched then rendered offline inside the sandbox, not
an unrestricted browser session with arbitrary navigation/subresources.
External-origin retrieval requires operator-enabled scoped egress and separate
acceptance testing; loopback test-origin checks are not production verification.

Installed offline tools work. Package downloads, dynamic live-site automation,
GPU support and external services must not be inferred from the presence of a
full-toolchain image. Member MCP/plugin adapters do not grant Admin management
permissions or access to global provider credentials.

## Persistent storage and request limits

Directory bind mounts plus `du` polling are not hard disk isolation. The local
backend charges entire backing filesystem capacities for writable managed
resources and bounded scratch reservations. Large ordinary server filesystems
can therefore be rejected under default quotas. Do not raise quotas to the
server disk size as a substitute for bounded storage.

An operator can provision a small bounded shared volume **before projects
exist**. Review the script and destination carefully; this privileged command
formats/mounts storage and was syntax-checked, not executed during verification.

```sh
sudo sh deployment/sandbox/provision-storage.sh \
  /srv/tomo/managed-storage /srv/tomo-managed.img 1000
# Review the printed persistent mount instructions and verify after reboot.
```

The script writes a `.tomo-bounded` marker. This is an operator configuration
signal, not independent proof of per-user kernel quotas or isolation. A shared
bounded volume has a global physical ceiling, not hard fairness between users.
For stronger per-user guarantees, provision and verify appropriately bounded
per-user storage and shared-project policies on the actual deployment host.

```sh
python -m app.runtime.storage --check
python -m app.runtime.storage --check --root /srv/tomo/managed-storage
python -m app.runtime.storage --provision
```

`--check` prints capability metadata and exits 2 without a bounded-volume marker.
`--provision` prints steps; it does not change host mounts. Private-write byte
accounting and upload reservations supplement, but do not replace, physical
storage enforcement. Request caps run before multipart parsing:

- `TOMO_MAX_REQUEST_BYTES`: default 32 MiB.
- `TOMO_MAX_UPLOAD_BYTES`: default 24 MiB.
- `TOMO_MAX_DB_MB`: default 2048 MiB control-plane DB cap.
- `TOMO_MAX_ATTACHMENTS_MB`: default 10240 MiB attachment cap.

## Remote execution is a release blocker

Remote context/generation/resource protocols and transfer plumbing exist, but
an operator marker alone does not establish an equivalent per-chat boundary.

- `connector/internal/executor/sandbox.go` advertises sandbox capability from a
  marker/image comparison. Actual per-chat namespaces, authorized mount sets,
  quotas and confirmed teardown must be implemented/verified independently.
- `app/workplaces/ssh_agent.py` currently starts host Bash with a scoped cwd and
  timeout. This is not a container, does not constrain command filesystem I/O
  to the cwd and does not enforce RO mount semantics. A matching marker must
  not be treated as proof of restricted SSH isolation.
- Actual remote host provisioning/handshake and multi-host containment were not
  exercised. Do not provision these advertised restricted paths for untrusted
  Members on the strength of the protocol tests.

Explicit unrestricted execution still requires destination-specific grants and
chat acknowledgement, with host-account effects accepted by the operator.
Offline/unsupported backends must reject without local fallback. Revocation
requires confirmed backend teardown; a failed acknowledgement retains the
pending barrier.

## Local verification

```sh
.venv/bin/pytest -n 0 -q tests/integration/test_multi_user_isolation.py
.venv/bin/pytest -n 0 -q tests/integration/test_multi_user_*.py
(cd connector && go test -count=1 ./...)
```

Image-dependent tests skip if the runtime/image is absent. Inspect skip counts;
passing protocol, SQLite or path-validation tests is not equivalent to passing
OS containment. See the verification document for full-suite commands, exact
local counts and remaining acceptance/security gates.
