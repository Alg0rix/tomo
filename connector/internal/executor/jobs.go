package executor

import (
	"crypto/rand"
	"encoding/hex"
	"fmt"
	"os"
	"os/exec"
	"strings"
	"sync"
	"sync/atomic"
	"time"
)

type bgJob struct {
	ID         string
	Command    string
	StartedAt  time.Time
	Cmd        *exec.Cmd
	Stdout     outputBuffer
	Stderr     outputBuffer
	Done       atomic.Bool
	ExitCode   atomic.Int32
	finishedAt time.Time
	processMu  sync.Mutex
}

const (
	maxJobs        = 128
	maxRunningJobs = 16
	jobTTL         = 15 * time.Minute
)

var (
	jobMu sync.Mutex
	jobs  = map[string]*bgJob{}
)

func processStart(params map[string]any) (any, error) {
	cmd := paramString(params, "command", "script")
	cwd := paramString(params, "cwd")
	return startBackgroundJob(cmd, cwd)
}

func processStatus(params map[string]any) (any, error) {
	id := paramString(params, "id")
	if id == "" {
		return nil, fmt.Errorf("'id' is required")
	}
	return getBackgroundJob(id)
}

func processKill(params map[string]any) (any, error) {
	id := paramString(params, "id")
	if id == "" {
		return nil, fmt.Errorf("'id' is required")
	}
	return killBackgroundJob(id)
}

func startBackgroundJob(command, cwd string) (map[string]any, error) {
	command = strings.TrimSpace(command)
	if command == "" {
		return nil, fmt.Errorf("'command' must be a non-empty string")
	}
	root := WorkRoot()
	if cwd == "" {
		cwd = strings.TrimRight(root, string(os.PathSeparator))
	} else {
		resolved, err := resolvePath(cwd, root)
		if err != nil {
			return nil, fmt.Errorf("cwd: %w", err)
		}
		cwd = resolved
	}
	jobMu.Lock()
	defer jobMu.Unlock()
	cleanupJobsLocked(time.Now())
	running := 0
	for _, j := range jobs {
		if !j.Done.Load() {
			running++
		}
	}
	if len(jobs) >= maxJobs || running >= maxRunningJobs {
		return nil, fmt.Errorf("busy: background job limit reached")
	}
	var randomID [16]byte
	if _, err := rand.Read(randomID[:]); err != nil {
		return nil, fmt.Errorf("generate job id: %w", err)
	}
	id := "job_" + hex.EncodeToString(randomID[:])
	// Agent workplace tooling intentionally runs shell scripts from the coordinator.
	// #nosec G204 -- command is the product surface (bash tool); cwd is jailed above.
	cmd := exec.Command("bash", "-lc", command) //nolint:gosec
	prepareProcess(cmd)
	cmd.Dir = cwd
	cmd.Env = os.Environ()

	job := &bgJob{ID: id, Command: command, StartedAt: time.Now(), Cmd: cmd}
	cmd.Stdout = &job.Stdout
	cmd.Stderr = &job.Stderr
	if err := cmd.Start(); err != nil {
		return nil, fmt.Errorf("could not start background command: %w", err)
	}
	jobs[id] = job
	go func() {
		// Wait's writer-copy goroutines finish before publishing Done.
		err := cmd.Wait()
		code := 0
		if err != nil {
			if ee, ok := err.(*exec.ExitError); ok {
				code = ee.ExitCode()
			} else {
				code = -1
			}
		}
		jobMu.Lock()
		job.ExitCode.Store(int32(code))
		job.finishedAt = time.Now()
		job.Done.Store(true)
		jobMu.Unlock()
	}()

	return map[string]any{
		"id":      id,
		"status":  "running",
		"command": command,
	}, nil
}

func cleanupJobsLocked(now time.Time) {
	for id, j := range jobs {
		if j.Done.Load() && now.Sub(j.finishedAt) >= jobTTL {
			delete(jobs, id)
		}
	}
}

func jobSnapshot(j *bgJob) map[string]any {
	status := "running"
	var rc any
	if j.Done.Load() {
		status = "exited"
		rc = int(j.ExitCode.Load())
	}
	return map[string]any{
		"id":         j.ID,
		"status":     status,
		"returncode": rc,
		"command":    j.Command,
		"stdout":     j.Stdout.String(),
		"stderr":     j.Stderr.String(),
	}
}

func listJobs() any {
	jobMu.Lock()
	defer jobMu.Unlock()
	cleanupJobsLocked(time.Now())
	out := make([]map[string]any, 0, len(jobs))
	for _, j := range jobs {
		out = append(out, jobSnapshot(j))
	}
	return out
}

func getBackgroundJob(id string) (map[string]any, error) {
	jobMu.Lock()
	cleanupJobsLocked(time.Now())
	j := jobs[id]
	jobMu.Unlock()
	if j == nil {
		return nil, fmt.Errorf("unknown job id %q", id)
	}
	return jobSnapshot(j), nil
}

func killBackgroundJob(id string) (map[string]any, error) {
	jobMu.Lock()
	cleanupJobsLocked(time.Now())
	j := jobs[id]
	jobMu.Unlock()
	if j == nil {
		return nil, fmt.Errorf("unknown job id %q", id)
	}
	j.processMu.Lock()
	if !j.Done.Load() && j.Cmd != nil && j.Cmd.Process != nil {
		if err := terminateProcess(j.Cmd); err != nil && err != os.ErrProcessDone {
			j.processMu.Unlock()
			return nil, fmt.Errorf("kill job: %w", err)
		}
	}
	j.processMu.Unlock()
	for i := 0; i < 20 && !j.Done.Load(); i++ {
		time.Sleep(50 * time.Millisecond)
	}
	return jobSnapshot(j), nil
}
