package main

import (
	"bytes"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"
)

const (
	maxOutputChars = 100_000
	defaultTimeout = 30 * time.Second
	maxTimeout     = 120 * time.Second
)

// workRoot is the jail root for file ops and bash cwd.
func workRoot() string {
	if v := os.Getenv("TOMO_CONNECTOR_ROOT"); v != "" {
		return v
	}
	home, err := connectorHome()
	if err != nil {
		return "."
	}
	root := filepath.Join(home, "work")
	_ = os.MkdirAll(root, 0o700)
	return root
}

func jailPath(root, rel string) (string, error) {
	rel = strings.TrimSpace(rel)
	if rel == "" {
		return "", fmt.Errorf("path must not be empty")
	}
	if strings.Contains(rel, "\x00") {
		return "", fmt.Errorf("path contains null byte")
	}
	if filepath.IsAbs(rel) {
		return "", fmt.Errorf("absolute paths are not allowed")
	}
	rootAbs, err := filepath.Abs(root)
	if err != nil {
		return "", err
	}
	rootAbs = filepath.Clean(rootAbs)
	target := filepath.Clean(filepath.Join(rootAbs, rel))
	// Ensure target is under root (with path separator edge).
	sep := string(os.PathSeparator)
	if target != rootAbs && !strings.HasPrefix(target, rootAbs+sep) {
		return "", fmt.Errorf("path escapes sandbox cwd")
	}
	return target, nil
}

func clip(s string) string {
	if len(s) <= maxOutputChars {
		return s
	}
	return s[:maxOutputChars] + fmt.Sprintf("\n...[truncated, %d chars total]", len(s))
}

func handleRPC(method string, params map[string]any) (string, error) {
	switch method {
	case "ping":
		return "pong", nil
	case "cwd_info":
		root := workRoot()
		abs, _ := filepath.Abs(root)
		return abs, nil
	case "bash":
		return rpcBash(params)
	case "read_file":
		return rpcReadFile(params)
	case "write_file":
		return rpcWriteFile(params)
	default:
		return "", fmt.Errorf("unknown method: %s", method)
	}
}

func rpcBash(params map[string]any) (string, error) {
	cmdStr, _ := params["command"].(string)
	cmdStr = strings.TrimSpace(cmdStr)
	if cmdStr == "" {
		return "", fmt.Errorf("'command' argument must be a non-empty string")
	}
	timeout := defaultTimeout
	if t, ok := asFloat(params["timeout"]); ok && t > 0 {
		timeout = time.Duration(t * float64(time.Second))
		if timeout > maxTimeout {
			timeout = maxTimeout
		}
	}
	root := workRoot()
	ctxDone := time.After(timeout)
	cmd := exec.Command("bash", "-lc", cmdStr)
	cmd.Dir = root
	var stdout, stderr bytes.Buffer
	cmd.Stdout = &stdout
	cmd.Stderr = &stderr
	if err := cmd.Start(); err != nil {
		return "", fmt.Errorf("could not run command: %w", err)
	}
	done := make(chan error, 1)
	go func() { done <- cmd.Wait() }()
	select {
	case <-ctxDone:
		_ = cmd.Process.Kill()
		return "", fmt.Errorf("command timed out after %gs", timeout.Seconds())
	case err := <-done:
		parts := []string{}
		if out := clip(stdout.String()); out != "" {
			parts = append(parts, strings.TrimRight(out, "\n"))
		}
		if errOut := clip(stderr.String()); errOut != "" {
			parts = append(parts, "stderr:\n"+strings.TrimRight(errOut, "\n"))
		}
		if err != nil {
			if ee, ok := err.(*exec.ExitError); ok {
				parts = append(parts, fmt.Sprintf("exit code: %d", ee.ExitCode()))
			} else {
				return "", err
			}
		}
		if len(parts) == 0 {
			return "(no output)", nil
		}
		return strings.Join(parts, "\n"), nil
	}
}

func rpcReadFile(params map[string]any) (string, error) {
	pathArg, _ := params["path"].(string)
	root := workRoot()
	target, err := jailPath(root, pathArg)
	if err != nil {
		return "", err
	}
	data, err := os.ReadFile(target)
	if err != nil {
		if os.IsNotExist(err) {
			return "", fmt.Errorf("file not found: %s", pathArg)
		}
		return "", fmt.Errorf("could not read file: %w", err)
	}
	// Reject obvious binary.
	n := len(data)
	if n > 8192 {
		n = 8192
	}
	for i := 0; i < n; i++ {
		if data[i] == 0 {
			return "", fmt.Errorf("binary files are not supported")
		}
	}
	text := string(data)
	if len(text) > maxOutputChars {
		return text[:maxOutputChars] + fmt.Sprintf("\n...[truncated, %d chars total]", len(text)), nil
	}
	return text, nil
}

func rpcWriteFile(params map[string]any) (string, error) {
	pathArg, _ := params["path"].(string)
	content, ok := params["content"].(string)
	if !ok {
		return "", fmt.Errorf("'content' argument must be a string")
	}
	root := workRoot()
	target, err := jailPath(root, pathArg)
	if err != nil {
		return "", err
	}
	if err := os.MkdirAll(filepath.Dir(target), 0o700); err != nil {
		return "", fmt.Errorf("could not write file: %w", err)
	}
	if err := os.WriteFile(target, []byte(content), 0o600); err != nil {
		return "", fmt.Errorf("could not write file: %w", err)
	}
	return fmt.Sprintf("Wrote %d bytes to %s", len(content), pathArg), nil
}

func asFloat(v any) (float64, bool) {
	switch t := v.(type) {
	case float64:
		return t, true
	case float32:
		return float64(t), true
	case int:
		return float64(t), true
	case int64:
		return float64(t), true
	case jsonNumber:
		f, err := t.Float64()
		return f, err == nil
	default:
		return 0, false
	}
}

// jsonNumber avoids importing encoding/json just for the interface name in tests.
type jsonNumber interface {
	Float64() (float64, error)
}
