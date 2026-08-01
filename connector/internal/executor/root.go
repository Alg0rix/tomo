package executor

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"github.com/tomo-project/tomo/connector/internal/state"
)

const (
	maxOutputBytes = 64 * 1024
	defaultTimeout = 60
	maxTimeoutSec  = 600
)

// WorkRoot is the jail root for file ops and default bash cwd.
func WorkRoot() string {
	if v := os.Getenv("TOMO_CONNECTOR_ROOT"); v != "" {
		return ensureTrailingSep(v)
	}
	home, err := state.Home()
	if err != nil {
		return ensureTrailingSep(".")
	}
	root := filepath.Join(home, "work")
	_ = os.MkdirAll(root, 0o700)
	return ensureTrailingSep(root)
}

func ensureTrailingSep(p string) string {
	clean := filepath.Clean(p)
	if !strings.HasSuffix(clean, string(os.PathSeparator)) {
		clean += string(os.PathSeparator)
	}
	return clean
}

// resolvePath — paths (relative or absolute) must stay under workDir.
func resolvePath(path, workDir string) (string, error) {
	path = strings.TrimSpace(path)
	if path == "" {
		return "", fmt.Errorf("path must not be empty")
	}
	if strings.Contains(path, "\x00") {
		return "", fmt.Errorf("path contains null byte")
	}
	var resolved string
	if filepath.IsAbs(path) {
		resolved = path
	} else {
		resolved = filepath.Join(workDir, path)
	}
	clean := filepath.Clean(resolved)
	workClean := filepath.Clean(strings.TrimRight(workDir, string(os.PathSeparator)))
	prefix := workClean + string(os.PathSeparator)
	if clean != workClean && !strings.HasPrefix(clean, prefix) {
		return "", fmt.Errorf("path escapes working directory: %s", path)
	}
	return clean, nil
}

func resolvePathAbs(path, workDir string) (string, error) {
	return resolvePath(path, workDir)
}

func truncate(s string) string {
	if len(s) <= maxOutputBytes {
		return s
	}
	return s[:maxOutputBytes] + "\n[truncated]"
}

func timeoutSec(v any) int {
	t := defaultTimeout
	if f, ok := asFloat(v); ok && f > 0 {
		t = int(f)
	}
	if t <= 0 || t > maxTimeoutSec {
		t = defaultTimeout
	}
	return t
}

func paramString(params map[string]any, keys ...string) string {
	for _, k := range keys {
		if s, ok := params[k].(string); ok {
			return s
		}
	}
	return ""
}

func paramEnv(params map[string]any) map[string]string {
	raw, ok := params["env"].(map[string]any)
	if !ok {
		return nil
	}
	out := make(map[string]string, len(raw))
	for k, v := range raw {
		out[k] = fmt.Sprint(v)
	}
	return out
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
	default:
		return 0, false
	}
}
