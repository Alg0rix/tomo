package executor

import (
	"bytes"
	"context"
	"fmt"
	"io"
	"os"
	"os/exec"
	"strings"
	"time"
)

// ExecResult is the structured bash/python reply.
type ExecResult struct {
	Stdout        string  `json:"stdout"`
	Stderr        string  `json:"stderr"`
	ExitCode      int     `json:"exit_code"`
	ExecutionTime float64 `json:"execution_time"`
}

func execBash(params map[string]any, progress Progress) (any, error) {
	script := strings.TrimSpace(paramString(params, "script", "command"))
	if script == "" {
		return nil, fmt.Errorf("'script' (or 'command') is required")
	}
	timeout := timeoutSec(params["timeout"])
	cwd := strings.TrimSpace(paramString(params, "cwd"))
	if cwd == "" {
		cwd = strings.TrimRight(WorkRoot(), string(os.PathSeparator))
	}
	if stream, _ := params["stream"].(bool); !stream {
		progress = nil
	}
	return runExec(timeout, cwd, paramEnv(params), "bash", "-s", script, progress)
}

func execPython(params map[string]any) (any, error) {
	code := strings.TrimSpace(paramString(params, "code"))
	if code == "" {
		return nil, fmt.Errorf("'code' is required")
	}
	timeout := timeoutSec(params["timeout"])
	cwd := strings.TrimSpace(paramString(params, "cwd"))
	if cwd == "" {
		cwd = strings.TrimRight(WorkRoot(), string(os.PathSeparator))
	}
	return runExec(timeout, cwd, paramEnv(params), "python3", "-", code, nil)
}

// runExec runs bin with stdin; when progress is non-nil, stdout and stderr are
// also forwarded live as they are produced.
func runExec(timeout int, cwd string, env map[string]string, bin, flag, stdin string, progress Progress) (ExecResult, error) {
	ctx, cancel := context.WithTimeout(context.Background(), time.Duration(timeout)*time.Second)
	defer cancel()

	cmd := exec.CommandContext(ctx, bin, flag)
	prepareProcess(cmd)
	cmd.Cancel = func() error { return terminateProcess(cmd) }
	cmd.Dir = cwd
	cmd.Stdin = bytes.NewBufferString(stdin)
	cmd.Env = os.Environ()
	for k, v := range env {
		cmd.Env = append(cmd.Env, k+"="+v)
	}

	var stdout, stderr outputBuffer
	cmd.Stdout = &stdout
	cmd.Stderr = &stderr
	if progress != nil {
		live := newStreamWriter(progress)
		defer live.Close()
		cmd.Stdout = io.MultiWriter(&stdout, live)
		cmd.Stderr = io.MultiWriter(&stderr, live)
	}

	t0 := time.Now()
	exitCode := 0
	if err := cmd.Run(); err != nil {
		if ctx.Err() != nil {
			return ExecResult{
				Stdout:        stdout.String(),
				Stderr:        stderr.String() + fmt.Sprintf("\nExecution timed out after %ds", timeout),
				ExitCode:      -1,
				ExecutionTime: time.Since(t0).Seconds(),
			}, nil
		}
		if ee, ok := err.(*exec.ExitError); ok {
			exitCode = ee.ExitCode()
		} else {
			return ExecResult{}, fmt.Errorf("could not run %s: %w", bin, err)
		}
	}
	return ExecResult{
		Stdout:        stdout.String(),
		Stderr:        stderr.String(),
		ExitCode:      exitCode,
		ExecutionTime: time.Since(t0).Seconds(),
	}, nil
}
