// Package executor implements connector JSON-RPC methods.
//
//	exec_bash / exec_python / read_file / write_file / str_replace /
//	delete_file / search_files / process_* / read_file_b64 / write_file_b64
package executor

import (
	"fmt"
	"os"
	"strings"
	"time"

	"github.com/tomo-project/tomo/connector/internal/clog"
)

// Handle dispatches method → result (JSON-serializable).
func Handle(method string, params map[string]any) (any, error) {
	if params == nil {
		params = map[string]any{}
	}
	t0 := time.Now()
	clog.Event("exec.start", "method", method, "params", clog.JSON(params, 400))
	var (
		result any
		err    error
	)
	switch method {
	case "ping":
		result = "pong"
	case "cwd_info":
		result = strings.TrimRight(WorkRoot(), string(os.PathSeparator))
	case "exec_bash", "bash":
		result, err = execBash(params)
	case "exec_python":
		result, err = execPython(params)
	case "read_file":
		result, err = readFile(params)
	case "write_file":
		result, err = writeFile(params)
	case "read_file_b64":
		result, err = readFileB64(params)
	case "write_file_b64":
		result, err = writeFileB64(params)
	case "str_replace":
		result, err = strReplace(params)
	case "delete_file":
		result, err = deleteFile(params)
	case "search_files":
		result, err = searchFiles(params)
	case "process_start":
		result, err = processStart(params)
	case "process_list":
		result = listJobs()
	case "process_status":
		result, err = processStatus(params)
	case "process_kill":
		result, err = processKill(params)
	default:
		err = fmt.Errorf("unknown method: %s", method)
	}
	ms := time.Since(t0).Milliseconds()
	if err != nil {
		clog.Error("exec.done", err, "method", method, "ok", false, "ms", ms)
	} else {
		clog.Event("exec.done", "method", method, "ok", true, "ms", ms, "result", clog.JSON(result, 400))
	}
	return result, err
}