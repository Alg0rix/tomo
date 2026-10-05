//go:build linux || darwin

package ws

import (
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/gorilla/websocket"
	"github.com/tomo-project/tomo/connector/internal/state"
)

func TestDisconnectWhileCommandRunsReplaysOnce(t *testing.T) {
	root := t.TempDir()
	t.Setenv("TOMO_CONNECTOR_ROOT", root)
	admitTestSession(t)
	store := testStore(t)
	dispatcher := newRPCDispatcher(store)
	defer dispatcher.close()
	done := make(chan error, 2)
	upgrader := websocket.Upgrader{}
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		c, err := upgrader.Upgrade(w, r, nil)
		if err != nil {
			return
		}
		defer c.Close()
		done <- serveLoop(c, &state.State{}, dispatcher, defaultHeartbeat)
	}))
	defer server.Close()
	dial := func() *websocket.Conn {
		c, _, err := websocket.DefaultDialer.Dial("ws"+strings.TrimPrefix(server.URL, "http"), nil)
		if err != nil {
			t.Fatal(err)
		}
		return c
	}
	old := dial()
	msg := request("in-flight", "exec_bash", withExecContext(map[string]any{"script": "echo started > started; sleep 0.3; printf x >> out"}))
	if err := old.WriteJSON(msg); err != nil {
		t.Fatal(err)
	}
	deadline := time.Now().Add(2 * time.Second)
	for {
		if _, err := os.Stat(filepath.Join(root, "wp_ws", "started")); err == nil {
			break
		}
		if time.Now().After(deadline) {
			t.Fatal("command never started")
		}
		time.Sleep(time.Millisecond)
	}
	_ = old.Close()
	select {
	case <-done:
	case <-time.After(time.Second):
		t.Fatal("old socket did not disconnect")
	}
	current := dial()
	defer current.Close()
	if err := current.WriteJSON(msg); err != nil {
		t.Fatal(err)
	}
	_ = current.SetReadDeadline(time.Now().Add(3 * time.Second))
	var out envelope
	if err := current.ReadJSON(&out); err != nil {
		t.Fatal(err)
	}
	if !out.OK {
		t.Fatal(out.Error)
	}
	raw, err := os.ReadFile(filepath.Join(root, "wp_ws", "out"))
	if err != nil || string(raw) != "x" {
		t.Fatalf("in-flight command repeated: %q %v", raw, err)
	}
	_ = current.Close()
	select {
	case <-done:
	case <-time.After(time.Second):
		t.Fatal("new socket did not disconnect")
	}
}
