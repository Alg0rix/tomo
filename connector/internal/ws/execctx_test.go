// Shared admitted-execution helper for ws journal/replay tests.
//
// The journal layer is transport; execution methods still carry a validated
// destination envelope (see executor/admission.go). These helpers admit one
// test session so replay/ordering tests exercise the real gate.
package ws

import (
	"testing"

	"github.com/tomo-project/tomo/connector/internal/executor"
)

func testEnvelope() map[string]any {
	return map[string]any{
		"v": 1, "owner_user_id": "u1", "session_id": "s1",
		"agent_id": "ops", "execution_mode": "restricted",
		"destination_id": "wp_ws", "active_workplace_id": "wp_ws",
		"access_generation": float64(1),
		"resources": []any{
			map[string]any{"workplace_id": "wp_ws", "permission": "read_write", "destination_id": "wp_ws"},
		},
		"quota": map[string]any{"duration_seconds": float64(60)},
	}
}

// admitTestSession registers (u1, s1) at generation 1 for wp_ws and returns
// the scope dir (<root>/wp_ws) where admitted files land.
func admitTestSession(t *testing.T) {
	t.Helper()
	executor.SetPairedWorkplaceID("wp_ws")
	t.Cleanup(func() { executor.SetPairedWorkplaceID("") })
	params := map[string]any{"exec_context": testEnvelope()}
	adm, err := executor.AdmitRequest("exec_admit", params)
	if err != nil {
		t.Fatalf("admit: %v", err)
	}
	if _, err := executor.HandleWithProgress("exec_admit", params, nil, adm); err != nil {
		t.Fatalf("exec_admit: %v", err)
	}
}

// withExecContext injects the test envelope into execution-method params.
func withExecContext(params map[string]any) map[string]any {
	out := make(map[string]any, len(params)+1)
	for k, v := range params {
		out[k] = v
	}
	out["exec_context"] = testEnvelope()
	return out
}
