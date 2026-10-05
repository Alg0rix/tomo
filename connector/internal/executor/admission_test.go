// Destination-owned execution contract tests: envelope validation,
// per-scope file enforcement, owner/generation isolation, supervised jobs
// with deadlines, confirmed teardown, and sandbox attestation.
package executor

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func testEnvelope(t *testing.T, owner, session, dest, active string, generation int, scopes ...map[string]any) map[string]any {
	t.Helper()
	resources := make([]any, 0, len(scopes))
	for _, sc := range scopes {
		resources = append(resources, sc)
	}
	return map[string]any{
		"exec_context": map[string]any{
			"v": 1, "owner_user_id": owner, "session_id": session,
			"agent_id": "ops", "execution_mode": "restricted",
			"destination_id": dest, "active_workplace_id": active,
			"access_generation": float64(generation),
			"resources":         resources,
			"quota":             map[string]any{"duration_seconds": float64(60)},
		},
	}
}

func rwScope(id string) map[string]any {
	return map[string]any{"workplace_id": id, "permission": "read_write", "destination_id": "wp_a"}
}

func roScope(id string) map[string]any {
	return map[string]any{"workplace_id": id, "permission": "read", "destination_id": "wp_a"}
}

func admitForTest(t *testing.T, method string, params map[string]any) *Admission {
	t.Helper()
	adm, err := AdmitRequest(method, params)
	if err != nil {
		t.Fatalf("admit %s: %v", method, err)
	}
	return adm
}

// admitScope registers (owner, session) for one writable test workplace and
// returns a live admission for follow-up calls. The destination scope dir
// is <root>/<workplace>.
func admitScope(t *testing.T, root, workplace, owner, session string) *Admission {
	t.Helper()
	t.Setenv("TOMO_CONNECTOR_ROOT", root)
	SetPairedWorkplaceID(workplace)
	t.Cleanup(func() { SetPairedWorkplaceID("") })
	resetAdmissions()
	scope := map[string]any{"workplace_id": workplace, "permission": "read_write", "destination_id": workplace}
	admit := testEnvelope(t, owner, session, workplace, workplace, 1, scope)
	raw, err := AdmitRequest("exec_admit", admit)
	if err != nil {
		t.Fatalf("admit: %v", err)
	}
	if _, err := HandleWithProgress("exec_admit", admit, nil, raw); err != nil {
		t.Fatalf("exec_admit: %v", err)
	}
	next := testEnvelope(t, owner, session, workplace, workplace, 1, scope)
	adm, err := AdmitRequest("exec_bash", next)
	if err != nil {
		t.Fatalf("admission not live: %v", err)
	}
	return adm
}

func TestAdmitRequiresEnvelope(t *testing.T) {
	SetPairedWorkplaceID("wp_a")
	defer SetPairedWorkplaceID("")
	if _, err := AdmitRequest("exec_bash", map[string]any{}); err == nil {
		t.Fatal("execution without envelope must fail")
	}
	if _, err := AdmitRequest("read_file", map[string]any{"path": "x"}); err == nil {
		t.Fatal("file op without envelope must fail")
	}
	if _, err := AdmitRequest("ping", map[string]any{}); err != nil {
		t.Fatalf("ping must stay open: %v", err)
	}
}

func TestAdmitDestinationMismatch(t *testing.T) {
	SetPairedWorkplaceID("wp_a")
	defer SetPairedWorkplaceID("")
	params := testEnvelope(t, "u1", "s1", "wp_OTHER", "wp_OTHER", 3, rwScope("wp_OTHER"))
	if _, err := AdmitRequest("exec_admit", params); err == nil {
		t.Fatal("envelope for another destination must fail")
	}
}

func TestAdmitRegistersAndStaleGenerationFails(t *testing.T) {
	SetPairedWorkplaceID("wp_a")
	defer SetPairedWorkplaceID("")
	resetAdmissions()
	params := testEnvelope(t, "u1", "s1", "wp_a", "wp_a", 3, rwScope("wp_a"))
	if _, err := HandleWithProgress("exec_admit", params, nil, admitForTest(t, "exec_admit", params)); err != nil {
		t.Fatalf("admit: %v", err)
	}
	execParams := testEnvelope(t, "u1", "s1", "wp_a", "wp_a", 3, rwScope("wp_a"))
	execParams["script"] = "true"
	if _, err := AdmitRequest("exec_bash", execParams); err != nil {
		t.Fatalf("current generation must validate: %v", err)
	}
	stale := testEnvelope(t, "u1", "s1", "wp_a", "wp_a", 2, rwScope("wp_a"))
	stale["script"] = "true"
	if _, err := AdmitRequest("exec_bash", stale); err == nil {
		t.Fatal("stale generation must fail after re-admission at a newer one")
	}
	newer := testEnvelope(t, "u1", "s1", "wp_a", "wp_a", 4, rwScope("wp_a"))
	if _, err := HandleWithProgress("exec_admit", newer, nil, admitForTest(t, "exec_admit", newer)); err != nil {
		t.Fatalf("re-admit: %v", err)
	}
	if _, err := AdmitRequest("exec_bash", execParams); err == nil {
		t.Fatal("older generation must fail after generation bump")
	}
}

func TestUnadmittedSessionRejected(t *testing.T) {
	SetPairedWorkplaceID("wp_a")
	defer SetPairedWorkplaceID("")
	resetAdmissions()
	params := testEnvelope(t, "ghost", "s9", "wp_a", "wp_a", 1, rwScope("wp_a"))
	params["script"] = "true"
	if _, err := AdmitRequest("exec_bash", params); err == nil {
		t.Fatal("unadmitted session must fail (admit first)")
	}
}

func TestFileEnforcementROAndEscape(t *testing.T) {
	root := t.TempDir()
	t.Setenv("TOMO_CONNECTOR_ROOT", root)
	SetPairedWorkplaceID("wp_a")
	defer SetPairedWorkplaceID("")
	resetAdmissions()
	admitParams := testEnvelope(t, "u1", "s1", "wp_a", "wp_a", 1, rwScope("wp_a"), roScope("wp_ro"))
	admRaw, err := AdmitRequest("exec_admit", admitParams)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := HandleWithProgress("exec_admit", admitParams, nil, admRaw); err != nil {
		t.Fatal(err)
	}
	// Write + read through the admitted RW scope.
	wparams := testEnvelope(t, "u1", "s1", "wp_a", "wp_a", 1, rwScope("wp_a"), roScope("wp_ro"))
	wparams["path"] = "note.txt"
	wparams["content"] = "hello"
	adm := admitForTest(t, "write_file", wparams)
	res, err := HandleWithProgress("write_file", wparams, nil, adm)
	if err != nil {
		t.Fatalf("write: %v", err)
	}
	got := res.(map[string]any)["path"].(string)
	if !strings.HasPrefix(got, filepath.Join(root, "wp_a")+string(os.PathSeparator)) {
		t.Fatalf("write escaped scope dir: %s", got)
	}
	rparams := testEnvelope(t, "u1", "s1", "wp_a", "wp_a", 1, rwScope("wp_a"), roScope("wp_ro"))
	rparams["path"] = filepath.Join(root, "wp_a", "note.txt")
	radm := admitForTest(t, "read_file", rparams)
	res, err = HandleWithProgress("read_file", rparams, nil, radm)
	if err != nil || res.(map[string]any)["content"] != "hello" {
		t.Fatalf("read: %v %v", res, err)
	}
	// Absolute coordinator-style paths outside admitted roots reject.
	esc := testEnvelope(t, "u1", "s1", "wp_a", "wp_a", 1, rwScope("wp_a"))
	esc["path"] = "/etc/passwd"
	if _, err := HandleWithProgress("read_file", esc, nil, admitForTest(t, "read_file", esc)); err == nil {
		t.Fatal("absolute host path must reject")
	}
	// Read-only scope rejects writes, allows reads.
	ro := testEnvelope(t, "u1", "s1", "wp_a", "wp_a", 1, rwScope("wp_a"), roScope("wp_ro"))
	roDir := filepath.Join(root, "wp_ro")
	if err := os.MkdirAll(roDir, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(roDir, "ref.txt"), []byte("r"), 0o644); err != nil {
		t.Fatal(err)
	}
	ro["path"] = "ref.txt"
	ro["content"] = "overwrite"
	_ = ro
	// Relative paths resolve against the ACTIVE scope (wp_a, writable)...
	// so address the RO file absolutely to prove RO denial.
	roAbs := testEnvelope(t, "u1", "s1", "wp_a", "wp_a", 1, rwScope("wp_a"), roScope("wp_ro"))
	roAbs["path"] = filepath.Join(roDir, "ref.txt")
	roAbs["content"] = "overwrite"
	if _, err := HandleWithProgress("write_file", roAbs, nil, admitForTest(t, "write_file", roAbs)); err == nil {
		t.Fatal("write to read-only scope must fail")
	}
	roRead := testEnvelope(t, "u1", "s1", "wp_a", "wp_a", 1, rwScope("wp_a"), roScope("wp_ro"))
	roRead["path"] = filepath.Join(roDir, "ref.txt")
	res, err = HandleWithProgress("read_file", roRead, nil, admitForTest(t, "read_file", roRead))
	if err != nil || res.(map[string]any)["content"] != "r" {
		t.Fatalf("read from RO scope: %v %v", res, err)
	}
	// Transfer-only scopes never become roots.
	xfer := testEnvelope(t, "u1", "s1", "wp_a", "wp_a", 1, map[string]any{
		"workplace_id": "wp_remote", "permission": "read_write",
		"destination_id": "wp_remote", "transfer_only": true,
	})
	xfer["path"] = filepath.Join(root, "wp_remote", "x.txt")
	xfer["content"] = "x"
	if _, err := HandleWithProgress("write_file", xfer, nil, admitForTest(t, "write_file", xfer)); err == nil {
		t.Fatal("transfer-only scope must never become a root")
	}
}

func TestExecCwdJailedAndCapped(t *testing.T) {
	root := t.TempDir()
	t.Setenv("TOMO_CONNECTOR_ROOT", root)
	SetPairedWorkplaceID("wp_a")
	defer SetPairedWorkplaceID("")
	resetAdmissions()
	admitParams := testEnvelope(t, "u1", "s1", "wp_a", "wp_a", 1, rwScope("wp_a"))
	admRaw, _ := AdmitRequest("exec_admit", admitParams)
	if _, err := HandleWithProgress("exec_admit", admitParams, nil, admRaw); err != nil {
		t.Fatal(err)
	}
	p := testEnvelope(t, "u1", "s1", "wp_a", "wp_a", 1, rwScope("wp_a"))
	p["script"] = "pwd"
	res, err := HandleWithProgress("exec_bash", p, nil, admitForTest(t, "exec_bash", p))
	if err != nil {
		t.Fatal(err)
	}
	if !strings.HasPrefix(res.(ExecResult).Stdout, filepath.Join(root, "wp_a")) {
		t.Fatalf("default cwd outside scope: %q", res.(ExecResult).Stdout)
	}
	evil := testEnvelope(t, "u1", "s1", "wp_a", "wp_a", 1, rwScope("wp_a"))
	evil["script"] = "true"
	evil["cwd"] = "/tmp"
	if _, err := HandleWithProgress("exec_bash", evil, nil, admitForTest(t, "exec_bash", evil)); err == nil {
		t.Fatal("cwd outside admitted roots must fail")
	}
}

func TestJobOwnerIsolationAndTeardown(t *testing.T) {
	root := t.TempDir()
	t.Setenv("TOMO_CONNECTOR_ROOT", root)
	SetPairedWorkplaceID("wp_a")
	defer SetPairedWorkplaceID("")
	resetAdmissions()
	mkadm := func(owner, session string) *Admission {
		p := testEnvelope(t, owner, session, "wp_a", "wp_a", 1, rwScope("wp_a"))
		raw, err := AdmitRequest("exec_admit", p)
		if err != nil {
			t.Fatal(err)
		}
		if _, err := HandleWithProgress("exec_admit", p, nil, raw); err != nil {
			t.Fatal(err)
		}
		p2 := testEnvelope(t, owner, session, "wp_a", "wp_a", 1, rwScope("wp_a"))
		p2["command"] = "sleep 30"
		p2["timeout"] = float64(30)
		return admitForTest(t, "process_start", p2)
	}
	admA := mkadm("alice", "s1")
	res, err := HandleWithProgress("process_start", map[string]any{
		"exec_context": testEnvelope(t, "alice", "s1", "wp_a", "wp_a", 1, rwScope("wp_a"))["exec_context"],
		"command":      "sleep 30", "timeout": float64(30),
	}, nil, admA)
	if err != nil {
		t.Fatalf("start: %v", err)
	}
	jobID := res.(map[string]any)["id"].(string)
	if res.(map[string]any)["remote_contract"] != RemoteExecContract {
		t.Fatalf("missing v2 attestation: %v", res)
	}
	// Another owner cannot observe or kill it.
	admB := mkadm("bob", "s2")
	statusParams := map[string]any{
		"exec_context": testEnvelope(t, "bob", "s2", "wp_a", "wp_a", 1, rwScope("wp_a"))["exec_context"],
		"id":           jobID,
	}
	if _, err := HandleWithProgress("process_status", statusParams, nil, admB); err == nil {
		t.Fatal("cross-owner status must fail")
	}
	if _, err := HandleWithProgress("process_kill", statusParams, nil, admB); err == nil {
		t.Fatal("cross-owner kill must fail")
	}
	// Owner kills it; teardown with nothing running still acknowledges.
	killParams := map[string]any{
		"exec_context": testEnvelope(t, "alice", "s1", "wp_a", "wp_a", 1, rwScope("wp_a"))["exec_context"],
		"id":           jobID,
	}
	if _, err := HandleWithProgress("process_kill", killParams, nil, admA); err != nil {
		t.Fatalf("owner kill: %v", err)
	}
	downParams := map[string]any{
		"exec_context": testEnvelope(t, "alice", "s1", "wp_a", "wp_a", 1, rwScope("wp_a"))["exec_context"],
	}
	down, err := HandleWithProgress("exec_teardown", downParams, nil, admA)
	if err != nil {
		t.Fatalf("teardown: %v", err)
	}
	if down.(map[string]any)["torn_down"] != true {
		t.Fatalf("teardown not acknowledged: %v", down)
	}
	// Post-teardown requests fail until re-admit.
	after := testEnvelope(t, "alice", "s1", "wp_a", "wp_a", 1, rwScope("wp_a"))
	after["script"] = "true"
	if _, err := AdmitRequest("exec_bash", after); err == nil {
		t.Fatal("requests after teardown must fail until re-admit")
	}
}

func TestSandboxMarkerGating(t *testing.T) {
	dir := t.TempDir()
	marker := filepath.Join(dir, "sandbox-marker")
	t.Setenv("TOMO_CONNECTOR_SANDBOX_MARKER", marker)
	t.Setenv("TOMO_CONNECTOR_SANDBOX_IMAGE", "sha256:abc123")
	if SandboxCapable() {
		t.Fatal("missing marker must not attest sandbox")
	}
	if err := os.WriteFile(marker, []byte("sha256:wrong\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	if SandboxCapable() {
		t.Fatal("mismatched digest must not attest sandbox")
	}
	if err := os.WriteFile(marker, []byte("sha256:abc123\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	if !SandboxCapable() {
		t.Fatal("matching marker must attest sandbox")
	}
	if SandboxImage() != "sha256:abc123" {
		t.Fatal("image digest not reported")
	}
}
