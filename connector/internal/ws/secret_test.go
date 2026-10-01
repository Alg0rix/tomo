package ws

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

func TestPrivateHTTPReplayNeverPersistsRepliesOrRepeatsAfterRestart(t *testing.T) {
	var hits atomic.Int32
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		hits.Add(1)
		_, _ = w.Write([]byte("synthetic-private-reply"))
	}))
	defer server.Close()
	store := testStore(t)
	msg := request("private-post", "secret_http", map[string]any{
		"url": server.URL, "method": "POST", "headers": map[string]any{}, "body_b64": "", "timeout": float64(5), "expires_at": float64(time.Now().Unix() + 60),
	})
	for i := 0; i < 2; i++ {
		if out := store.execute(msg); !out.OK {
			t.Fatal(out.Error)
		}
	}
	if hits.Load() != 1 {
		t.Fatal("private request repeated")
	}
	raw, err := os.ReadFile(filepath.Join(store.dir, hashBytes([]byte(msg.ID))+".json"))
	if err != nil {
		t.Fatal(err)
	}
	var record cacheRecord
	if err = json.Unmarshal(raw, &record); err != nil {
		t.Fatal(err)
	}
	if record.Completed || len(record.Response) != 0 || strings.Contains(string(raw), "body_b64") {
		t.Fatal("private result persisted")
	}
	store.close()
	restarted, err := openRPCStore(store.dir)
	if err != nil {
		t.Fatal(err)
	}
	defer restarted.close()
	out := restarted.execute(msg)
	if out.OK || !strings.Contains(out.Error, "uncertain") || hits.Load() != 1 {
		t.Fatal("uncertain private POST was executed again")
	}
}

func TestBrokerBridgeRefusesPublicPlaintextAndNonBrokerRoutes(t *testing.T) {
	if srv, address, err := startBroker("http://example.test"); err != nil || srv != nil || address != "" {
		t.Fatal("plaintext remote broker enabled")
	}
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "Bearer synthetic-capability" {
			t.Error("authorization lost")
		}
		_, _ = w.Write([]byte(`{"bundles":[]}`))
	}))
	defer upstream.Close()
	srv, address, err := startBroker(upstream.URL)
	if err != nil {
		t.Fatal(err)
	}
	defer srv.Close()
	req, _ := http.NewRequest("GET", address+"/api/secret-broker/bundles", nil)
	req.Header.Set("Authorization", "Bearer synthetic-capability")
	response, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	response.Body.Close()
	if response.StatusCode != 200 {
		t.Fatal("broker route failed")
	}
	req, _ = http.NewRequest("GET", address+"/api/sessions/other/secrets", nil)
	req.Header.Set("Authorization", "Bearer synthetic-capability")
	response, err = http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	response.Body.Close()
	if response.StatusCode != 403 {
		t.Fatal("bridge forwarded browser/other routes")
	}
}
