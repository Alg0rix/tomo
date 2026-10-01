package executor

import (
	"encoding/base64"
	"os"
	"path/filepath"
	"testing"
	"time"
)

func TestSecretFileWriteDetectsConcurrentChangesAndExpires(t *testing.T) {
	root := t.TempDir()
	t.Setenv("TOMO_CONNECTOR_ROOT", root)
	path := filepath.Join(root, ".env")
	if err := os.WriteFile(path, []byte("PUBLIC=first\n"), 0600); err != nil {
		t.Fatal(err)
	}
	params := map[string]any{"path": path, "expires_at": float64(time.Now().Unix() + 60)}
	read, err := Handle("secret_file_read", params)
	if err != nil {
		t.Fatal(err)
	}
	params["digest"] = read.(map[string]any)["digest"]
	params["content_b64"] = base64.StdEncoding.EncodeToString([]byte("KEY=synthetic-private\n"))
	if err := os.WriteFile(path, []byte("PUBLIC=changed\n"), 0600); err != nil {
		t.Fatal(err)
	}
	if _, err = Handle("secret_file_write", params); err == nil {
		t.Fatal("concurrent file overwritten")
	}
	raw, _ := os.ReadFile(path)
	if string(raw) != "PUBLIC=changed\n" {
		t.Fatal("partial private write")
	}
	read, err = Handle("secret_file_read", params)
	if err != nil {
		t.Fatal(err)
	}
	params["digest"] = read.(map[string]any)["digest"]
	params["expires_at"] = float64(time.Now().Unix() - 1)
	if _, err = Handle("secret_file_write", params); err == nil {
		t.Fatal("expired private write accepted")
	}
	raw, _ = os.ReadFile(path)
	if string(raw) != "PUBLIC=changed\n" {
		t.Fatal("expired operation changed file")
	}
}
