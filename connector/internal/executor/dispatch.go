// Package executor implements connector JSON-RPC methods.
//
//	exec_bash / exec_python / read_file / write_file / str_replace /
//	delete_file / search_files / process_* / read_file_b64 / write_file_b64
package executor

import (
	"fmt"
	"os"
	"strings"
)

// Handle dispatches method → result (JSON-serializable).
func Handle(method string, params map[string]any) (any, error) {
	if params == nil {
		params = map[string]any{}
	}
	switch method {
	case "ping":
		return "pong", nil
	case "cwd_info":
		return strings.TrimRight(WorkRoot(), string(os.PathSeparator)), nil
	case "exec_bash", "bash":
		return execBash(params)
	case "exec_python":
		return execPython(params)
	case "read_file":
		return readFile(params)
	case "write_file":
		return writeFile(params)
	case "read_file_b64":
		return readFileB64(params)
	case "write_file_b64":
		return writeFileB64(params)
	case "str_replace":
		return strReplace(params)
	case "delete_file":
		return deleteFile(params)
	case "search_files":
		return searchFiles(params)
	case "process_start":
		return processStart(params)
	case "process_list":
		return listJobs(), nil
	case "process_status":
		return processStatus(params)
	case "process_kill":
		return processKill(params)
	default:
		return nil, fmt.Errorf("unknown method: %s", method)
	}
}