package executor

import (
	"fmt"
	"os"
	"os/exec"
	"strings"
	"sync"
	"sync/atomic"
	"time"
)

type bgJob struct {
	ID        string
	Command   string
	StartedAt time.Time
	Cmd       *exec.Cmd
	Stdout    strings.Builder
	Stderr    strings.Builder
	Done      atomic.Bool
	ExitCode  atomic.Int32
}

var (
	jobMu      sync.Mutex
	jobs       = map[string]*bgJob{}
	jobCounter atomic.Uint64
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
	if cwd == "" {
		cwd = strings.TrimRight(WorkRoot(), string(os.PathSeparator))
	}
	id := fmt.Sprintf("job_%d", jobCounter.Add(1))
	cmd := exec.Command("bash", "-lc", command)
	cmd.Dir = cwd
	cmd.Env = os.Environ()

	job := &bgJob{ID: id, Command: command, StartedAt: time.Now(), Cmd: cmd}
	stdout, err := cmd.StdoutPipe()
	if err != nil {
		return nil, err
	}
	stderr, err := cmd.StderrPipe()
	if err != nil {
		return nil, err
	}
	if err := cmd.Start(); err != nil {
		return nil, fmt.Errorf("could not start background command: %w", err)
	}

	jobMu.Lock()
	jobs[id] = job
	jobMu.Unlock()

	go drain(stdout, &job.Stdout)
	go drain(stderr, &job.Stderr)
	go func() {
		err := cmd.Wait()
		code := 0
		if err != nil {
			if ee, ok := err.(*exec.ExitError); ok {
				code = ee.ExitCode()
			} else {
				code = -1
			}
		}
		job.ExitCode.Store(int32(code))
		job.Done.Store(true)
	}()

	return map[string]any{
		"id":      id,
		"status":  "running",
		"command": command,
	}, nil
}

func drain(r interface{ Read([]byte) (int, error) }, b *strings.Builder) {
	buf := make([]byte, 4096)
	for {
		n, err := r.Read(buf)
		if n > 0 {
			b.Write(buf[:n])
		}
		if err != nil {
			return
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
		"stdout":     truncate(j.Stdout.String()),
		"stderr":     truncate(j.Stderr.String()),
	}
}

func listJobs() any {
	jobMu.Lock()
	defer jobMu.Unlock()
	out := make([]map[string]any, 0, len(jobs))
	for _, j := range jobs {
		out = append(out, jobSnapshot(j))
	}
	return out
}

func getBackgroundJob(id string) (map[string]any, error) {
	jobMu.Lock()
	j := jobs[id]
	jobMu.Unlock()
	if j == nil {
		return nil, fmt.Errorf("unknown job id %q", id)
	}
	return jobSnapshot(j), nil
}

func killBackgroundJob(id string) (map[string]any, error) {
	jobMu.Lock()
	j := jobs[id]
	jobMu.Unlock()
	if j == nil {
		return nil, fmt.Errorf("unknown job id %q", id)
	}
	if !j.Done.Load() && j.Cmd != nil && j.Cmd.Process != nil {
		_ = j.Cmd.Process.Kill()
	}
	for i := 0; i < 20 && !j.Done.Load(); i++ {
		time.Sleep(50 * time.Millisecond)
	}
	return jobSnapshot(j), nil
}
