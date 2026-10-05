//go:build linux || darwin

package ws

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/gorilla/websocket"
	"github.com/tomo-project/tomo/connector/internal/state"
)

func TestExecBashStreamSendsProgressBeforeResponse(t *testing.T) {
	t.Setenv("TOMO_CONNECTOR_ROOT", t.TempDir())
	dispatcher := newRPCDispatcher(testStore(t))
	defer dispatcher.close()
	upgrader := websocket.Upgrader{}
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		c, err := upgrader.Upgrade(w, r, nil)
		if err != nil {
			return
		}
		defer c.Close()
		_ = serveLoop(c, &state.State{}, dispatcher, defaultHeartbeat)
	}))
	defer server.Close()
	conn, _, err := websocket.DefaultDialer.Dial("ws"+strings.TrimPrefix(server.URL, "http"), nil)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()

	admitTestSession(t)
	if err := conn.WriteJSON(request("live", "exec_bash", withExecContext(map[string]any{
		"script": "echo one; sleep 0.4; echo two", "stream": true,
	}))); err != nil {
		t.Fatal(err)
	}
	_ = conn.SetReadDeadline(time.Now().Add(5 * time.Second))
	var streamed strings.Builder
	var firstAt time.Time
	start := time.Now()
	for {
		var in struct {
			Type   string         `json:"type"`
			ID     string         `json:"id"`
			OK     bool           `json:"ok"`
			Result map[string]any `json:"result"`
		}
		if err := conn.ReadJSON(&in); err != nil {
			t.Fatal(err)
		}
		if in.ID != "live" {
			t.Fatalf("unexpected id %q", in.ID)
		}
		if in.Type == "rpc_progress" {
			if firstAt.IsZero() {
				firstAt = time.Now()
			}
			streamed.WriteString(in.Result["data"].(string))
			continue
		}
		if in.Type != "rpc_response" || !in.OK || in.Result["stdout"] != "one\ntwo\n" {
			t.Fatalf("bad response: %+v", in)
		}
		break
	}
	if streamed.String() != "one\ntwo\n" {
		t.Fatalf("streamed %q", streamed.String())
	}
	if firstAt.IsZero() || firstAt.Sub(start) > 300*time.Millisecond {
		t.Fatalf("first chunk not live: %v", firstAt.Sub(start))
	}
}
