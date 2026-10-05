// Package executor implements connector JSON-RPC methods with
// destination-owned execution contracts.
//
// Every execution RPC must carry params["exec_context"]: an immutable
// envelope minted by the coordinator (owner user, owning session/agent,
// execution mode, destination id, access generation, enabled resources with
// permissions, quota slice). The destination validates the envelope before
// acting and enforces it per request:
//
//   - destination id must match this connector's paired workplace;
//   - the (owner, session) registration from exec_admit must exist and its
//     generation must equal the envelope generation (grant revocation bumps
//     the generation, which fails closed here);
//   - file paths must resolve under an admitted resource root, with
//     read-only roots rejecting writes;
//   - background jobs are tagged by owner/session and observable/killable
//     only by the same owner/session; exec_teardown kills them with ack.
//
// Connectors that cannot enforce this must not be trusted with supervised
// execution: the coordinator refuses them (see remote_contract.go).
package executor

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"sync"
)

// RemoteExecContract is the supervised execution contract version attested
// in exec_admit responses and background job snapshots.
const RemoteExecContract = 2

// AdmittedRoot is one coordinator-enabled resource scope at the destination.
// Scope is the workplace id; the connector owns the path layout (see
// DestRoots), so coordinator-side paths can never become jail roots.
type AdmittedRoot struct {
	Scope    string
	Path     string
	Writable bool
}

// Admission is one validated execution envelope for a single RPC.
type Admission struct {
	Owner       string
	Session     string
	Agent       string
	Mode        string // "restricted" | "unrestricted"
	Destination string
	Active      string // active workplace id (default cwd scope)
	Generation  int
	Roots       []AdmittedRoot
	TimeoutSec  int
}

// destScopeName maps an admitted workplace id to its destination-owned
// subtree. The coordinator never sends destination paths: this connector
// owns the layout, so a compromised coordinator path cannot escape it.
func destScopeName(workplaceID string) string {
	clean := strings.TrimSpace(workplaceID)
	if clean == "" {
		return ""
	}
	for _, c := range clean {
		if !(c >= 'a' && c <= 'z' || c >= 'A' && c <= 'Z' || c >= '0' && c <= '9' || c == '-' || c == '_') {
			return ""
		}
	}
	return clean
}

// DestRoots resolves the admitted workplace scopes under the connector work
// root, creating them on first use. Coordinator-side paths in the envelope
// are audit metadata only and never used as filesystem roots.
func (adm *Admission) DestRoots() []AdmittedRoot {
	base := strings.TrimRight(WorkRoot(), string(os.PathSeparator))
	out := make([]AdmittedRoot, 0, len(adm.Roots))
	for _, r := range adm.Roots {
		name := destScopeName(r.Scope)
		if name == "" {
			continue
		}
		dir := filepath.Join(base, name)
		_ = os.MkdirAll(dir, 0o700)
		out = append(out, AdmittedRoot{Scope: r.Scope, Path: dir, Writable: r.Writable})
	}
	return out
}

type sessionRecord struct {
	generation  int
	mode        string
	destination string
}

var (
	admitMu   sync.Mutex
	admitted  = map[string]sessionRecord{}
	sandboxMu sync.Mutex

	pairedMu sync.Mutex
	pairedID = ""
)

// SetPairedWorkplaceID binds destination checks to this connector's
// workplace (called at daemon startup; tests set it directly).
func SetPairedWorkplaceID(id string) {
	pairedMu.Lock()
	pairedID = strings.TrimSpace(id)
	pairedMu.Unlock()
}

func pairedWorkplaceID() string {
	pairedMu.Lock()
	id := pairedID
	pairedMu.Unlock()
	return id
}

func sessionKey(owner, session string) string { return owner + "\x00" + session }

func asInt(v any) (int, bool) {
	switch t := v.(type) {
	case float64:
		return int(t), true
	case float32:
		return int(t), true
	case int:
		return t, true
	case int64:
		return int(t), true
	default:
		return 0, false
	}
}

func asStringMap(v any) (map[string]any, bool) {
	if m, ok := v.(map[string]any); ok {
		return m, true
	}
	return nil, false
}

// execContext extracts the envelope without registering it.
func execContext(params map[string]any) (map[string]any, error) {
	raw, ok := params["exec_context"]
	if !ok || raw == nil {
		return nil, fmt.Errorf("exec_context is required (update the coordinator)")
	}
	env, ok := asStringMap(raw)
	if !ok {
		return nil, fmt.Errorf("exec_context must be an object")
	}
	if v, _ := asInt(env["v"]); v != 1 {
		return nil, fmt.Errorf("unsupported exec_context version")
	}
	return env, nil
}

// validateEnvelope checks fields and destination binding, returning the
// admission without touching the registration table.
func validateEnvelope(params map[string]any, workplaceID string) (*Admission, error) {
	env, err := execContext(params)
	if err != nil {
		return nil, err
	}
	str := func(key string) string {
		s, _ := env[key].(string)
		return strings.TrimSpace(s)
	}
	owner, session, agent := str("owner_user_id"), str("session_id"), str("agent_id")
	mode, dest := str("execution_mode"), str("destination_id")
	if owner == "" || session == "" || dest == "" {
		return nil, fmt.Errorf("exec_context missing owner, session or destination")
	}
	if mode != "restricted" && mode != "unrestricted" {
		return nil, fmt.Errorf("exec_context has unknown execution mode")
	}
	if workplaceID != "" && dest != workplaceID {
		return nil, fmt.Errorf("exec_context destination does not match this connector")
	}
	gen, _ := asInt(env["access_generation"])
	if gen < 0 {
		return nil, fmt.Errorf("exec_context has invalid generation")
	}
	rawRoots, _ := env["resources"].([]any)
	roots := make([]AdmittedRoot, 0, len(rawRoots))
	for _, item := range rawRoots {
		rm, ok := asStringMap(item)
		if !ok {
			continue
		}
		transfer, _ := rm["transfer_only"].(bool)
		if transfer {
			// Transfer-only endpoints live on other machines; they are
			// never execution roots at this destination.
			continue
		}
		scope, _ := rm["workplace_id"].(string)
		scope = strings.TrimSpace(scope)
		if destScopeName(scope) == "" {
			continue
		}
		perm, _ := rm["permission"].(string)
		roots = append(roots, AdmittedRoot{Scope: scope, Writable: perm == "read_write"})
	}
	timeout := 0
	if quota, ok := asStringMap(env["quota"]); ok {
		if d, ok := asInt(quota["duration_seconds"]); ok && d > 0 {
			timeout = d
		}
	}
	active, _ := env["active_workplace_id"].(string)
	return &Admission{
		Owner: owner, Session: session, Agent: agent, Mode: mode,
		Destination: dest, Active: strings.TrimSpace(active),
		Generation: gen, Roots: roots, TimeoutSec: timeout,
	}, nil
}

// AdmitRequest validates the envelope for method and returns the admission.
// "ping" stays open (connectivity only, reveals nothing). "exec_admit" and
// "exec_teardown" validate fields + destination without requiring a prior
// registration; every other execution method requires a live registration
// whose generation equals the envelope generation.
//
// Teardown needs no registration and no generation equality: with nothing
// admitted there is nothing to kill (ack trivially); after revocation the
// registration belongs to an older generation but the kill still runs scoped
// to (owner, session). Killing can never escalate.
func AdmitRequest(method string, params map[string]any) (*Admission, error) {
	if method == "ping" {
		return nil, nil
	}
	adm, err := validateEnvelope(params, pairedWorkplaceID())
	if err != nil {
		return nil, err
	}
	if method == "exec_admit" || method == "exec_teardown" {
		return adm, nil
	}
	admitMu.Lock()
	rec, ok := admitted[sessionKey(adm.Owner, adm.Session)]
	admitMu.Unlock()
	if !ok {
		return nil, fmt.Errorf("no live execution admission for this session (call exec_admit first)")
	}
	if rec.destination != adm.Destination {
		return nil, fmt.Errorf("execution admission is for another destination")
	}
	if rec.generation != adm.Generation {
		return nil, fmt.Errorf("stale execution generation: access changed; re-admit")
	}
	return adm, nil
}

// admitSession registers (owner, session) at the envelope generation.
func admitSession(adm *Admission) {
	admitMu.Lock()
	admitted[sessionKey(adm.Owner, adm.Session)] = sessionRecord{
		generation: adm.Generation, mode: adm.Mode, destination: adm.Destination,
	}
	admitMu.Unlock()
}

// forgetSession removes the registration; returns whether one existed.
func forgetSession(owner, session string) bool {
	admitMu.Lock()
	key := sessionKey(owner, session)
	_, ok := admitted[key]
	if ok {
		delete(admitted, key)
	}
	admitMu.Unlock()
	return ok
}

// resetAdmissions drops all registrations (tests only).
func resetAdmissions() {
	admitMu.Lock()
	admitted = map[string]sessionRecord{}
	admitMu.Unlock()
}

// AuthorizePath resolves path under the admitted destination roots with
// read-only enforcement and symlink containment. Relative paths resolve
// against the active workplace scope; absolute paths must fall under an
// admitted root. Writes to read-only scopes are rejected; nothing may
// escape its root.
func (adm *Admission) AuthorizePath(path string, write bool) (string, error) {
	if adm == nil {
		return "", fmt.Errorf("execution admission is required")
	}
	path = strings.TrimSpace(path)
	if path == "" {
		return "", fmt.Errorf("path must not be empty")
	}
	if strings.Contains(path, "\x00") {
		return "", fmt.Errorf("path contains null byte")
	}
	for _, root := range adm.DestRoots() {
		clean := filepath.Clean(root.Path)
		var resolved string
		if filepath.IsAbs(path) {
			resolved = filepath.Clean(path)
		} else {
			active := clean
			for _, candidate := range adm.DestRoots() {
				if candidate.Scope == adm.Active {
					active = filepath.Clean(candidate.Path)
					break
				}
			}
			resolved = filepath.Clean(filepath.Join(active, path))
		}
		prefix := clean + string(os.PathSeparator)
		if resolved != clean && !strings.HasPrefix(resolved, prefix) {
			continue
		}
		if write && !root.Writable {
			return "", fmt.Errorf("path is read-only in this execution scope")
		}
		// Lexical containment is not enough: a symlink inside the root may
		// point at the wider host. Contain the real parent like secretPath.
		realParent, err := filepath.EvalSymlinks(filepath.Dir(resolved))
		if err != nil {
			// Missing parents are created by writers under the real root.
			parent := filepath.Dir(resolved)
			for {
				if info, err := os.Lstat(parent); err == nil {
					rp, err := filepath.EvalSymlinks(parent)
					if err != nil {
						return "", fmt.Errorf("path escapes execution root")
					}
					_ = info
					if rp != clean && !strings.HasPrefix(rp, prefix) {
						return "", fmt.Errorf("path escapes execution root")
					}
					break
				}
				next := filepath.Dir(parent)
				if next == parent {
					return "", fmt.Errorf("path escapes execution root")
				}
				parent = next
			}
			return resolved, nil
		}
		real := filepath.Join(realParent, filepath.Base(resolved))
		if real != clean && !strings.HasPrefix(real, prefix) {
			return "", fmt.Errorf("path escapes execution root")
		}
		return real, nil
	}
	return "", fmt.Errorf("path is outside admitted execution roots")
}

// AuthorizeCwd validates a working directory under the admitted roots.
func (adm *Admission) AuthorizeCwd(cwd string) (string, error) {
	if strings.TrimSpace(cwd) == "" {
		return adm.AuthorizePath(".", false)
	}
	return adm.AuthorizePath(cwd, false)
}

// CapTimeout bounds a requested timeout by the admitted quota slice.
func (adm *Admission) CapTimeout(want int) int {
	if want <= 0 {
		want = defaultTimeout
	}
	if want > maxTimeoutSec {
		want = maxTimeoutSec
	}
	if adm.TimeoutSec > 0 && want > adm.TimeoutSec {
		want = adm.TimeoutSec
	}
	return want
}
