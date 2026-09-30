package ws

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/gorilla/websocket"
	"github.com/tomo-project/tomo/connector/internal/state"
)

func testStore(t *testing.T) *rpcStore {
	t.Helper()
	s, err := openRPCStore(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(s.close)
	return s
}
func request(id, method string, params map[string]any) envelope {
	return envelope{V: 1, Type: "rpc_request", ID: id, Method: method, Params: params}
}

func TestConcurrentReplayAndRestart(t *testing.T) {
	root := t.TempDir()
	t.Setenv("TOMO_CONNECTOR_ROOT", root)
	store := testStore(t)
	msg := request("append-once", "write_file", map[string]any{"path": "out", "content": "x", "mode": "append"})
	var wg sync.WaitGroup
	for i := 0; i < 12; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			if out := store.execute(msg); !out.OK {
				t.Errorf("execute: %s", out.Error)
			}
		}()
	}
	wg.Wait()
	store.close()
	restarted, err := openRPCStore(store.dir)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(restarted.close)
	if out := restarted.execute(msg); !out.OK {
		t.Fatal(out.Error)
	}
	raw, err := os.ReadFile(filepath.Join(root, "out"))
	if err != nil || string(raw) != "x" {
		t.Fatalf("mutation repeated: %q %v", raw, err)
	}
	msg.Params["content"] = "different"
	if out := restarted.execute(msg); out.OK || !strings.Contains(out.Error, "reused") {
		t.Fatal("id collision accepted")
	}
}

func TestUncertainReplayNeverExecutes(t *testing.T) {
	root := t.TempDir()
	t.Setenv("TOMO_CONNECTOR_ROOT", root)
	store := testStore(t)
	msg := request("interrupted", "write_file", map[string]any{"path": "out", "content": "x"})
	raw, _ := json.Marshal(struct {
		Method string
		Params map[string]any
	}{msg.Method, msg.Params})
	if err := store.save(hashBytes([]byte(msg.ID)), cacheRecord{Fingerprint: hashBytes(raw), Expires: time.Now().Add(rpcCacheTTL)}); err != nil {
		t.Fatal(err)
	}
	store.close()
	restarted, err := openRPCStore(store.dir)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(restarted.close)
	out := restarted.execute(msg)
	if out.OK || !strings.Contains(out.Error, "uncertain") {
		t.Fatalf("bad replay: %+v", out)
	}
	if _, err := os.Stat(filepath.Join(root, "out")); !os.IsNotExist(err) {
		t.Fatal("uncertain mutation executed")
	}
}

func TestCacheLimitsExpiryAndInvalidRequests(t *testing.T) {
	store := testStore(t)
	if out := store.execute(request("", "ping", nil)); out.OK {
		t.Fatal("empty id accepted")
	}
	bad := request("bad-version", "ping", nil)
	bad.V = 9
	if out := store.execute(bad); out.OK {
		t.Fatal("bad version accepted")
	}
	for i := 0; i < maxCacheEntries; i++ {
		id := time.Unix(int64(i), 0).String()
		if out := store.execute(request(id, "ping", nil)); !out.OK {
			t.Fatal(out.Error)
		}
	}
	if out := store.execute(request("overflow", "ping", nil)); !strings.Contains(out.Error, "busy") {
		t.Fatal("cache capacity not enforced")
	}
	store.mu.Lock()
	for _, e := range store.entries {
		e.record.Expires = time.Now().Add(-time.Second)
	}
	store.mu.Unlock()
	if out := store.execute(request("after-expiry", "ping", nil)); !out.OK {
		t.Fatal(out.Error)
	}
	files, _ := os.ReadDir(store.dir)
	if len(files) != 2 {
		t.Fatalf("expired disk records retained: %d", len(files))
	}
}

func TestCacheIntentPersistenceFailureDoesNotExecute(t *testing.T) {
	root := t.TempDir()
	t.Setenv("TOMO_CONNECTOR_ROOT", root)
	store := testStore(t)
	if err := os.RemoveAll(store.dir); err != nil {
		t.Fatal(err)
	}
	out := store.execute(request("failed", "write_file", map[string]any{"path": "out", "content": "x"}))
	if out.OK || !strings.Contains(out.Error, "not executed") {
		t.Fatal(out)
	}
	if _, err := os.Stat(filepath.Join(root, "out")); !os.IsNotExist(err) {
		t.Fatal("mutation executed without journal")
	}
}

func TestHeartbeatAndReconnectReplay(t *testing.T) {
	store := testStore(t)
	dispatcher := newRPCDispatcher(store)
	defer dispatcher.close()
	heartbeat := heartbeatConfig{20 * time.Millisecond, 200 * time.Millisecond, 100 * time.Millisecond}
	done := make(chan error, 2)
	upgrader := websocket.Upgrader{}
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		conn, err := upgrader.Upgrade(w, r, nil)
		if err != nil {
			return
		}
		defer conn.Close()
		done <- serveLoop(conn, &state.State{}, dispatcher, heartbeat)
	}))
	defer server.Close()
	dial := func() *websocket.Conn {
		c, _, err := websocket.DefaultDialer.Dial("ws"+strings.TrimPrefix(server.URL, "http"), nil)
		if err != nil {
			t.Fatal(err)
		}
		return c
	}
	conn := dial()
	// Read pings but deliberately never acknowledge: transport traffic is not pong.
	_ = conn.SetReadDeadline(time.Now().Add(2 * time.Second))
	for {
		var msg envelope
		if err := conn.ReadJSON(&msg); err != nil {
			break
		}
	}
	_ = conn.Close()
	select {
	case err := <-done:
		if err == nil {
			t.Fatal("dead heartbeat accepted")
		}
	case <-time.After(2 * time.Second):
		t.Fatal("heartbeat did not close socket")
	}
	// Reconnect and replay a previously completed mutation.
	root := t.TempDir()
	t.Setenv("TOMO_CONNECTOR_ROOT", root)
	msg := request("reconnect", "write_file", map[string]any{"path": "out", "content": "x", "mode": "append"})
	if out := store.execute(msg); !out.OK {
		t.Fatal(out)
	}
	conn = dial()
	defer conn.Close()
	if err := conn.WriteJSON(msg); err != nil {
		t.Fatal(err)
	}
	_ = conn.SetReadDeadline(time.Now().Add(2 * time.Second))
	for {
		var out envelope
		if err := conn.ReadJSON(&out); err != nil {
			t.Fatal(err)
		}
		if out.Type == "rpc_response" {
			if !out.OK {
				t.Fatal(out.Error)
			}
			break
		}
		if out.Type == "ping" {
			_ = conn.WriteJSON(envelope{V: 1, Type: "pong"})
		}
	}
	raw, _ := os.ReadFile(filepath.Join(root, "out"))
	if string(raw) != "x" {
		t.Fatalf("replayed side effect: %q", raw)
	}
	_ = conn.Close()
	select {
	case <-done:
	case <-time.After(2 * time.Second):
		t.Fatal("serve loop failed to stop")
	}
}

func TestQueueBusy(t *testing.T) {
	// No workers: exercise admission deterministically through a real socket.
	upgrader := websocket.Upgrader{}
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		c, err := upgrader.Upgrade(w, r, nil)
		if err != nil {
			return
		}
		defer c.Close()
		d := &rpcDispatcher{queue: make(chan rpcTask, 1)}
		d.queue <- rpcTask{}
		d.submit(&socketWriter{conn: c, timeout: time.Second}, request("busy", "ping", nil))
	}))
	defer server.Close()
	c, _, err := websocket.DefaultDialer.Dial("ws"+strings.TrimPrefix(server.URL, "http"), nil)
	if err != nil {
		t.Fatal(err)
	}
	defer c.Close()
	var out envelope
	if err := c.ReadJSON(&out); err != nil {
		t.Fatal(err)
	}
	if out.OK || !strings.Contains(out.Error, "busy") {
		t.Fatal(out)
	}
}

func TestJournalExclusiveAndCorruptionFailsClosed(t *testing.T) {
	store := testStore(t)
	if second, err := openRPCStore(store.dir); err == nil {
		second.close()
		t.Fatal("two daemons can use same replay journal")
	}
	store.close()
	if err := os.WriteFile(filepath.Join(store.dir, "broken.json"), []byte("{broken"), 0600); err != nil {
		t.Fatal(err)
	}
	if reopened, err := openRPCStore(store.dir); err == nil {
		reopened.close()
		t.Fatal("corrupt journal accepted")
	}
}

func TestWriteDeadlineClosesBlockedSocket(t *testing.T) {
	release := make(chan struct{})
	upgraded := make(chan struct{})
	upgrader := websocket.Upgrader{}
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		c, err := upgrader.Upgrade(w, r, nil)
		if err != nil {
			return
		}
		defer c.Close()
		close(upgraded)
		// Deliberately never read: a large outbound frame must hit its deadline.
		<-release
	}))
	defer server.Close()
	defer close(release)
	c, _, err := websocket.DefaultDialer.Dial("ws"+strings.TrimPrefix(server.URL, "http"), nil)
	if err != nil {
		t.Fatal(err)
	}
	defer c.Close()
	<-upgraded
	writer := &socketWriter{conn: c, timeout: 50 * time.Millisecond}
	start := time.Now()
	err = writer.send(envelope{V: 1, Type: "rpc_response", Result: strings.Repeat("x", 16<<20)})
	if err == nil {
		t.Fatal("blocked write succeeded without peer reading")
	}
	if time.Since(start) > 2*time.Second {
		t.Fatal("write deadline was not respected")
	}
	if err := writer.send(envelope{V: 1, Type: "ping"}); err == nil {
		t.Fatal("failed write did not close socket")
	}
}

func TestCacheByteBudgetAndResponseLimit(t *testing.T) {
	root := t.TempDir()
	t.Setenv("TOMO_CONNECTOR_ROOT", root)
	store := testStore(t)
	store.bytes = maxCacheBytes
	if out := store.execute(request("over-budget", "write_file", map[string]any{"path": "should-not-exist", "content": "x"})); out.OK || !strings.Contains(out.Error, "busy") {
		t.Fatal(out)
	}
	if _, err := os.Stat(filepath.Join(root, "should-not-exist")); !os.IsNotExist(err) {
		t.Fatal("cache-full request executed")
	}
	store.bytes = 0
	raw := []byte(strings.Repeat("x", maxResponseBytes+1))
	if err := os.WriteFile(filepath.Join(root, "large"), raw, 0600); err != nil {
		t.Fatal(err)
	}
	msg := request("oversized-result", "read_file", map[string]any{"path": "large"})
	out := store.execute(msg)
	if out.OK || !strings.Contains(out.Error, "storage limit") {
		t.Fatal("oversized result was retained")
	}
	// The saved error replays even if the file's contents subsequently change.
	if err := os.WriteFile(filepath.Join(root, "large"), []byte("small"), 0600); err != nil {
		t.Fatal(err)
	}
	if replay := store.execute(msg); replay.OK || replay.Error != out.Error {
		t.Fatal("oversized response was not deduplicated")
	}
	if store.bytes > 1024 {
		t.Fatalf("oversized payload retained: %d bytes", store.bytes)
	}
}
