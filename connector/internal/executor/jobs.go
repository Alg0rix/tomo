package executor

import (
	"crypto/rand"
	"encoding/hex"
	"fmt"
	"io"
	"os"
	"os/exec"
	"regexp"
	"strings"
	"sync"
	"sync/atomic"
	"time"
)

type bgJob struct {
	ID            string
	Command       string
	StartedAt     time.Time
	Cmd           *exec.Cmd
	Stdout        jobOutputBuffer
	Stderr        jobOutputBuffer
	Done          atomic.Bool
	ExitCode      atomic.Int32
	finishedAt    time.Time
	processMu     sync.Mutex
	MainExited    atomic.Bool
	StopRequested atomic.Bool
	CleanupFailed atomic.Bool
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
	return startBackgroundJobWithID(cmd, cwd, paramString(params, "id"))
}

func processStatus(params map[string]any) (any, error) {
	id := paramString(params, "id")
	if id == "__contract__" {
		return map[string]any{"process_contract": backgroundJobContract()}, nil
	}
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
	return startBackgroundJobWithID(command, cwd, "")
}

func startBackgroundJobWithID(command, cwd, id string) (map[string]any, error) {
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
	if id != "" {
		if !regexp.MustCompile(`^job_[0-9a-f]{32}$`).MatchString(id) {
			return nil, fmt.Errorf("invalid background job handle")
		}
		if existing := jobs[id]; existing != nil {
			if existing.Command != command || existing.Cmd.Dir != cwd {
				return nil, fmt.Errorf("job id already belongs to a different command")
			}
			return jobSnapshot(existing), nil
		}
	}
	running := 0
	for _, j := range jobs {
		if !j.Done.Load() || j.CleanupFailed.Load() {
			running++
		}
	}
	if len(jobs) >= maxJobs || running >= maxRunningJobs {
		return nil, fmt.Errorf("busy: background job limit reached")
	}
	if id == "" {
		var randomID [16]byte
		if _, err := rand.Read(randomID[:]); err != nil {
			return nil, fmt.Errorf("generate job id: %w", err)
		}
		id = "job_" + hex.EncodeToString(randomID[:])
	}
	// Agent workplace tooling intentionally runs shell scripts from the coordinator.
	// #nosec G204 -- command is the product surface (bash tool); cwd is jailed above.
	cmd := exec.Command("bash", "-lc", command) //nolint:gosec
	prepareProcess(cmd)
	cmd.Dir = cwd
	cmd.Env = os.Environ()

	job := &bgJob{ID: id, Command: command, StartedAt: time.Now(), Cmd: cmd}
	// Use our own pipes: exec.Cmd's implicit writer goroutines can otherwise
	// wait forever for a descendant even though the command shell has exited.
	outReader, outWriter, err := os.Pipe()
	if err != nil {
		return nil, err
	}
	errReader, errWriter, err := os.Pipe()
	if err != nil {
		outReader.Close()
		outWriter.Close()
		return nil, err
	}
	cmd.Stdout = outWriter
	cmd.Stderr = errWriter
	if err := cmd.Start(); err != nil {
		outReader.Close()
		outWriter.Close()
		errReader.Close()
		errWriter.Close()
		return nil, fmt.Errorf("could not start background command: %w", err)
	}
	outWriter.Close()
	errWriter.Close()
	jobs[id] = job
	var readers sync.WaitGroup
	readers.Add(2)
	go func() { defer readers.Done(); _, _ = io.Copy(&job.Stdout, outReader) }()
	go func() { defer readers.Done(); _, _ = io.Copy(&job.Stderr, errReader) }()
	go func() {
		err := cmd.Wait()
		job.MainExited.Store(true)
		code := 0
		if err != nil {
			if ee, ok := err.(*exec.ExitError); ok {
				code = ee.ExitCode()
			} else {
				code = -1
			}
		}
		job.processMu.Lock()
		if err := cleanupBackgroundGroup(cmd); err != nil {
			job.CleanupFailed.Store(true)
		}
		job.processMu.Unlock()
		drained := make(chan struct{})
		go func() { readers.Wait(); close(drained) }()
		select {
		case <-drained:
		case <-time.After(time.Second):
			outReader.Close()
			errReader.Close()
			<-drained
		}
		outReader.Close()
		errReader.Close()
		jobMu.Lock()
		job.ExitCode.Store(int32(code))
		job.finishedAt = time.Now()
		job.Done.Store(true)
		jobMu.Unlock()
	}()

	return map[string]any{
		"id":               id,
		"status":           "running",
		"command":          command,
		"process_contract": backgroundJobContract(),
	}, nil
}

func cleanupJobsLocked(now time.Time) {
	for id, j := range jobs {
		if j.Done.Load() && !j.CleanupFailed.Load() && now.Sub(j.finishedAt) >= jobTTL {
			delete(jobs, id)
		}
	}
}

func jobSnapshot(j *bgJob) map[string]any {
	status := "running"
	if j.StopRequested.Load() && !j.MainExited.Load() {
		status = "stopping"
	}
	var rc any
	if j.Done.Load() {
		status = "exited"
		rc = int(j.ExitCode.Load())
		if j.StopRequested.Load() && j.ExitCode.Load() < 0 {
			status = "stopped"
		}
		if j.CleanupFailed.Load() {
			status = "unknown"
			rc = nil
		}
	}
	return map[string]any{
		"id":               j.ID,
		"process_contract": backgroundJobContract(),
		"truncated":        j.Stdout.Truncated() || j.Stderr.Truncated(),
		"reason":           cleanupReason(j),
		"status":           status,
		"returncode":       rc,
		"command":          j.Command,
		"stdout":           j.Stdout.String(),
		"stderr":           j.Stderr.String(),
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
	if j.CleanupFailed.Load() {
		if err := cleanupBackgroundGroup(j.Cmd); err != nil {
			j.processMu.Unlock()
			return jobSnapshot(j), nil
		}
		j.CleanupFailed.Store(false)
	}
	if !j.MainExited.Load() && !j.Done.Load() && j.Cmd != nil && j.Cmd.Process != nil {
		j.StopRequested.Store(true)
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

// Background jobs retain the most recent output while draining all bytes.
type jobOutputBuffer struct {
	mu        sync.Mutex
	data      []byte
	truncated bool
}

func (b *jobOutputBuffer) Write(p []byte) (int, error) {
	b.mu.Lock()
	defer b.mu.Unlock()
	n := len(p)
	b.data = append(b.data, p...)
	if len(b.data) > maxOutputBytes {
		b.data = append([]byte(nil), b.data[len(b.data)-maxOutputBytes:]...)
		b.truncated = true
	}
	return n, nil
}
func (b *jobOutputBuffer) String() string {
	b.mu.Lock()
	defer b.mu.Unlock()
	return string(b.data)
}
func (b *jobOutputBuffer) Truncated() bool {
	b.mu.Lock()
	defer b.mu.Unlock()
	return b.truncated
}

func cleanupReason(j *bgJob) string {
	if j.CleanupFailed.Load() {
		return "process group cleanup could not be confirmed"
	}
	return ""
}
