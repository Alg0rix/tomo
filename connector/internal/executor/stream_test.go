//go:build linux || darwin

package executor

import (
	"strings"
	"sync"
	"testing"
	"time"
	"unicode/utf8"
)

func TestExecBashStreamsBeforeExit(t *testing.T) {
	var mu sync.Mutex
	var chunks []string
	var firstAt time.Time
	progress := func(c string) {
		mu.Lock()
		defer mu.Unlock()
		if firstAt.IsZero() && strings.Contains(c, "first") {
			firstAt = time.Now()
		}
		chunks = append(chunks, c)
	}
	start := time.Now()
	adm := admitScope(t, t.TempDir(), "wp_a", "u1", "s1")
	env := testEnvelope(t, "u1", "s1", "wp_a", "wp_a", 1, rwScope("wp_a"))
	env["script"] = "echo first; sleep 0.6; echo 'søcond ✓' >&2"
	env["stream"] = true
	res, err := execBash(env, progress, adm)
	if err != nil {
		t.Fatal(err)
	}
	finished := time.Since(start)
	out := res.(ExecResult)
	if out.Stdout != "first\n" || out.Stderr != "søcond ✓\n" {
		t.Fatalf("result changed: %+v", out)
	}
	mu.Lock()
	defer mu.Unlock()
	if got := strings.Join(chunks, ""); got != "first\nsøcond ✓\n" {
		t.Fatalf("streamed %q", got)
	}
	for _, c := range chunks {
		if !utf8.ValidString(c) {
			t.Fatalf("chunk split a rune: %q", c)
		}
	}
	if firstAt.IsZero() || firstAt.Sub(start) > 400*time.Millisecond || finished < 500*time.Millisecond {
		t.Fatalf("not live: first=%v total=%v", firstAt.Sub(start), finished)
	}
}

func TestExecBashWithoutStreamFlagSendsNothing(t *testing.T) {
	called := false
	adm := admitScope(t, t.TempDir(), "wp_a", "u1", "s1")
	env2 := testEnvelope(t, "u1", "s1", "wp_a", "wp_a", 1, rwScope("wp_a"))
	env2["script"] = "echo hi"
	if _, err := execBash(env2, func(string) { called = true }, adm); err != nil {
		t.Fatal(err)
	}
	if called {
		t.Fatal("progress must stay off unless the server asked for it")
	}
}

func TestStreamWriterHoldsIncompleteRune(t *testing.T) {
	var mu sync.Mutex
	var got []string
	s := newStreamWriter(func(c string) { mu.Lock(); got = append(got, c); mu.Unlock() })
	check := []byte("✓")
	_, _ = s.Write(check[:1])
	s.flush(false)
	_, _ = s.Write(check[1:])
	s.Close()
	mu.Lock()
	defer mu.Unlock()
	if strings.Join(got, "") != "✓" || len(got) != 1 {
		t.Fatalf("got %q", got)
	}
}
