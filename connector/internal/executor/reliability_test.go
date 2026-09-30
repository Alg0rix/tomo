//go:build linux || darwin

package executor

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"
)

func TestOutputBufferBoundedConcurrent(t *testing.T) {
	var b outputBuffer
	var wg sync.WaitGroup
	for i := 0; i < 4; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for j := 0; j < 100; j++ {
				p := []byte(strings.Repeat("x", 4096))
				n, err := b.Write(p)
				if n != len(p) || err != nil {
					t.Error("write must drain all input")
				}
				_ = b.String()
			}
		}()
	}
	wg.Wait()
	if len(b.data) != maxOutputBytes || !strings.HasSuffix(b.String(), "\n[truncated]") {
		t.Fatalf("output not bounded: %d", len(b.data))
	}
}

func TestExecLargeOutput(t *testing.T) {
	out, err := runExec(5, t.TempDir(), nil, "bash", "-s", "head -c 2000000 /dev/zero | tr '\\0' x; printf done >&2", nil)
	if err != nil {
		t.Fatal(err)
	}
	if out.ExitCode != 0 || out.Stderr != "done" || len(out.Stdout) != maxOutputBytes+len("\n[truncated]") {
		t.Fatalf("bad output: exit=%d length=%d stderr=%q", out.ExitCode, len(out.Stdout), out.Stderr)
	}
}

func TestTimeoutKillsChildrenAndReturns(t *testing.T) {
	dir := t.TempDir()
	start := time.Now()
	out, err := runExec(1, dir, nil, "bash", "-s", "(trap '' TERM; sleep 2; echo alive > escaped) & echo started; wait", nil)
	if err != nil {
		t.Fatal(err)
	}
	if out.ExitCode != -1 || !strings.Contains(out.Stdout, "started") {
		t.Fatalf("timeout result: %+v", out)
	}
	if time.Since(start) > 4*time.Second {
		t.Fatal("timeout hung on descendant pipe")
	}
	time.Sleep(1200 * time.Millisecond)
	if _, err := os.Stat(filepath.Join(dir, "escaped")); !os.IsNotExist(err) {
		t.Fatal("child survived timeout")
	}
}

func TestBackgroundJobPollingAndFinalOutput(t *testing.T) {
	t.Setenv("TOMO_CONNECTOR_ROOT", t.TempDir())
	result, err := startBackgroundJob("for i in $(seq 1 1000); do printf x; done; printf FINAL >&2", "")
	if err != nil {
		t.Fatal(err)
	}
	id := result["id"].(string)
	deadline := time.Now().Add(5 * time.Second)
	for time.Now().Before(deadline) {
		snapshot, err := getBackgroundJob(id)
		if err != nil {
			t.Fatal(err)
		}
		_ = listJobs()
		if snapshot["status"] == "exited" {
			if snapshot["stderr"] != "FINAL" || len(snapshot["stdout"].(string)) != 1000 {
				t.Fatalf("lost output: %v", snapshot)
			}
			return
		}
		time.Sleep(time.Millisecond)
	}
	t.Fatal("job failed to finish")
}

func TestBackgroundKillChildren(t *testing.T) {
	dir := t.TempDir()
	t.Setenv("TOMO_CONNECTOR_ROOT", dir)
	result, err := startBackgroundJob("(trap '' TERM; sleep 2; echo alive > escaped) & echo ready; wait", "")
	if err != nil {
		t.Fatal(err)
	}
	id := result["id"].(string)
	deadline := time.Now().Add(3 * time.Second)
	for {
		snap, _ := getBackgroundJob(id)
		if strings.Contains(snap["stdout"].(string), "ready") {
			break
		}
		if time.Now().After(deadline) {
			t.Fatal("job never ready")
		}
		time.Sleep(5 * time.Millisecond)
	}
	snap, err := killBackgroundJob(id)
	if err != nil || snap["status"] != "exited" {
		t.Fatalf("kill: %v %v", snap, err)
	}
	time.Sleep(2100 * time.Millisecond)
	if _, err := os.Stat(filepath.Join(dir, "escaped")); !os.IsNotExist(err) {
		t.Fatal("background child survived kill")
	}
}

func TestJobRetentionAndAdmission(t *testing.T) {
	t.Setenv("TOMO_CONNECTOR_ROOT", t.TempDir())
	jobMu.Lock()
	original := jobs
	jobs = make(map[string]*bgJob)
	expired := &bgJob{finishedAt: time.Now().Add(-jobTTL)}
	expired.Done.Store(true)
	jobs["expired"] = expired
	cleanupJobsLocked(time.Now())
	if len(jobs) != 0 {
		t.Error("expired job retained")
	}
	for i := 0; i < maxRunningJobs; i++ {
		jobs[fmt.Sprint(i)] = &bgJob{}
	}
	jobMu.Unlock()
	defer func() { jobMu.Lock(); jobs = original; jobMu.Unlock() }()
	if _, err := startBackgroundJob("true", ""); err == nil || !strings.Contains(err.Error(), "busy") {
		t.Fatalf("running limit: %v", err)
	}
	jobMu.Lock()
	for _, j := range jobs {
		j.finishedAt = time.Now()
		j.Done.Store(true)
	}
	for i := maxRunningJobs; i < maxJobs; i++ {
		j := &bgJob{finishedAt: time.Now()}
		j.Done.Store(true)
		jobs[fmt.Sprint(i)] = j
	}
	jobMu.Unlock()
	if _, err := startBackgroundJob("true", ""); err == nil {
		t.Fatal("retained job limit ignored")
	}
}
