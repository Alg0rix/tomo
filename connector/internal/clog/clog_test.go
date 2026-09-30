package clog

import (
	"bytes"
	"log"
	"strings"
	"testing"
)

func TestEventRedactsPayloadAndCredentials(t *testing.T) {
	var out bytes.Buffer
	old := logger
	logger = log.New(&out, "", 0)
	defer func() { logger = old }()
	for _, key := range []string{"params", "result", "body", "preview", "script", "command", "env", "content", "data", "token", "code", "stdout", "stderr"} {
		Event("test", key, "private-value", "id", "rpc-123", "ms", 10)
	}
	if strings.Contains(out.String(), "private-value") {
		t.Fatal("sensitive log value leaked")
	}
	if !strings.Contains(out.String(), "id=rpc-123") || !strings.Contains(out.String(), "ms=10") {
		t.Fatal("diagnostic metadata lost")
	}
}
