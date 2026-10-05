# Multi-user verification and release gates

Approved design: [multi-user workplace access](../superpowers/specs/2026-10-04-multi-user-workplace-access-design.md).
Current scope and blockers: [implementation status](2026-10-04-multi-user-completion.md).

## Latest local verification

| Check | Result |
| --- | --- |
| Full Python suite, serial temporary-state run | 1,256 passed, 0 failed, 1 Pydantic warning |
| 11-file multi-user integration set | 127 passed |
| Real Docker isolation module | 17 passed, no skips on the successful run |
| Go connector, `go test -count=1 ./...` | Passed |
| Node tests | 12 passed, 0 failed |
| Changed Python Ruff, compile, diff whitespace, shell/JS syntax | Passed |
| Headless Member login/home/account/chat UI | Passed, no page errors |

A Docker test runner wrapper crashed on the first attempt; the immediate rerun passed all 17 tests. Raw logs, browser screenshots, local runner/probe scripts and workflow files were archived outside the checkout before committing. They are not dependencies of the application or committed tests.

After cleanup, a fresh bounded serial run again passed **1,256 tests in 315.88 seconds**, with the same 1 Pydantic warning and no skips/failures. Go and Node (12 tests) passed again. Ruff passed for all 206 changed Python files; syntax checks passed for all 9 changed JavaScript files, compileall, the provisioning script and diff whitespace. Generated artifacts remained outside the repository.

## Reproduce

Use the development environment and a disposable test state. `tests/conftest.py` isolates the application's test home. Real-container tests require a running local Docker daemon and the `tomo:sandbox` image built using the [deployment instructions](../../deployment/sandbox/README.md); image-dependent tests skip explicitly if unavailable, so inspect the final skip count.

```sh
.venv/bin/pytest -n 0 -q
.venv/bin/pytest -n 0 -q tests/integration/test_multi_user_*.py
.venv/bin/pytest -n 0 -q tests/integration/test_multi_user_isolation.py
(cd connector && go test -count=1 ./...)
node --test tests/js/*.test.cjs
.venv/bin/python -m compileall -q app cli tests
sh -n deployment/sandbox/provision-storage.sh
git diff --check
```

Earlier full-suite runs used an external Linux-only pytest watchdog (20 seconds per test, timeouts fail rather than skip). It is a local harness artifact, not a repository dependency. Use an explicit overall timeout in automated environments; don't suppress failures or count skipped containment tests as verification.

## Test coverage

- `test_multi_user_access.py`: real SQLite roles, migration, assignments, private/shared resources, delegation and revocation barriers.
- `test_multi_user_http.py`: cookie/API-key ownership, private-object mutations and pending-request/grant IDOR checks.
- `test_multi_user_runtime.py`: current identity, context propagation, model/tool ceilings and durable execution.
- `test_multi_user_isolation.py`: real local Docker mounts, RO files, symlinks, full toolchain, private environments, resource limits, persistence and revocation.
- `test_multi_user_quotas.py`: shared admission, request-body limits and storage accounting.
- `test_multi_user_member_terminals.py`: held-container PTYs, ownership, quota and revocation behavior.
- `test_multi_user_member_tools.py`: local sandbox MCP/plugin execution and personal skills/state.
- `test_multi_user_browser_ssh.py`: offline/scoped rendering and local SSH-agent protocol. Protocol success is **not proof of SSH OS isolation**.
- `test_multi_user_remote.py`: destination contract, generation checks, offline rejection and bounded transfers. Marker/transport tests are **not proof of per-chat remote containment**.
- `test_multi_user_final_acceptance.py`: supported local concurrency, cross-user isolation, quota teardown and model revocation.
- `test_multi_user_ui.py`: UI scope and controls; separate headless flows exercised rendered Member pages.

Legacy tests now establish explicit authorized identities rather than anonymous host execution. Tunnel mock tests were replaced with verified-destination protocol tests; SSH tests require grants/acknowledgement, and workplace hints cannot widen the execution ceiling. The earlier chat MCP positive test became an unsupported-service denial test; separate scoped MCP tests exercise supported positive calls. These assertion changes must not be represented as proof that all original workflows or containment requirements are unchanged.

## Not established by the green suite

- Restricted SSH currently launches host Bash; a marker does not provide a per-chat OS boundary or RO mount enforcement.
- Remote connector marker attestation does not independently establish destination per-chat isolation.
- Production filesystem provisioning and hard per-user disk fairness, rootless Podman and separate broker topology are unverified.
- Fetch-then-render browser coverage does not establish arbitrary live website automation.
- Real external destinations/origins, full swarm/channel acceptance and sustained concurrency stress remain unverified.
- Independent security reviews rejected by their providers remain incomplete. Subsequent source inspection and functional tests do not substitute for a completed adversarial or multi-host audit.

Do not deploy this branch to untrusted Members until these implementation and acceptance gates are resolved.
