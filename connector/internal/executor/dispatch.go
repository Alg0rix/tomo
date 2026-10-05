// Package executor implements connector JSON-RPC methods with
// destination-owned execution contracts (see admission.go).
//
//	exec_bash / exec_python / read_file / write_file / str_replace / patch /
//	delete_file / search_files / process_* / read_file_b64 / write_file_b64 /
//	exec_admit / exec_teardown
package executor

import (
	"fmt"
	"os"
	"strings"
	"time"

	"github.com/tomo-project/tomo/connector/internal/clog"
)

// HandleWithProgress dispatches method → result (JSON-serializable).
// adm is the validated admission for this request (nil only for "ping").
// progress carries live output for exec_bash requests that set "stream".
func HandleWithProgress(method string, params map[string]any, progress Progress, adm *Admission) (any, error) {
	if params == nil {
		params = map[string]any{}
	}
	if method != "ping" && method != "exec_admit" && adm == nil {
		return nil, fmt.Errorf("execution admission is required")
	}
	t0 := time.Now()
	clog.Event("exec.start", "method", method)
	var (
		result any
		err    error
	)
	switch method {
	case "ping":
		result = "pong"
	case "exec_admit":
		result, err = execAdmit(params, adm)
	case "exec_teardown":
		result, err = execTeardown(params, adm)
	case "cwd_info":
		cwd, cerr := adm.AuthorizeCwd("")
		if cerr != nil {
			err = cerr
			break
		}
		result = strings.TrimRight(cwd, string(os.PathSeparator))
	case "exec_bash", "bash":
		result, err = execBash(params, progress, adm)
	case "exec_python":
		result, err = execPython(params, adm)
	case "secret_file_read":
		result, err = secretFileRead(params, adm)
	case "secret_file_write":
		result, err = secretFileWrite(params, adm)
	case "secret_http":
		result, err = secretHTTP(params, adm)
	case "read_file":
		result, err = readFile(params, adm)
	case "write_file":
		result, err = writeFile(params, adm)
	case "read_file_b64":
		result, err = readFileB64(params, adm)
	case "write_file_b64":
		result, err = writeFileB64(params, adm)
	case "str_replace":
		result, err = strReplace(params, adm)
	case "patch":
		result, err = applyPatch(params, adm)
	case "delete_file":
		result, err = deleteFile(params, adm)
	case "search_files":
		result, err = searchFiles(params, adm)
	case "list_dir":
		result, err = listDir(params, adm)
	case "process_start":
		result, err = processStart(params, adm)
	case "process_list":
		result = listJobs(adm)
	case "process_status":
		result, err = processStatus(params, adm)
	case "process_kill":
		result, err = processKill(params, adm)
	default:
		err = fmt.Errorf("unknown method: %s", method)
	}
	ms := time.Since(t0).Milliseconds()
	if err != nil {
		clog.Event("exec.done", "method", method, "ok", false, "ms", ms)
	} else {
		clog.Event("exec.done", "method", method, "ok", true, "ms", ms)
	}
	return result, err
}

// execAdmit registers the validated envelope's (owner, session) at its
// generation and attests the destination contract.
func execAdmit(params map[string]any, adm *Admission) (any, error) {
	if adm == nil {
		return nil, fmt.Errorf("exec_context is required")
	}
	admitSession(adm)
	return map[string]any{
		"contract":    RemoteExecContract,
		"mode":        adm.Mode,
		"destination": adm.Destination,
		"owner":       adm.Owner,
		"session":     adm.Session,
		"generation":  adm.Generation,
		"sandbox":     SandboxCapable(),
		"sandbox_img": SandboxImage(),
	}, nil
}

// execTeardown kills the envelope's owner/session jobs and drops its
// registration, acknowledging confirmed teardown.
func execTeardown(params map[string]any, adm *Admission) (any, error) {
	if adm == nil {
		return nil, fmt.Errorf("exec_context is required")
	}
	killed := killSessionJobs(adm.Owner, adm.Session)
	forgetSession(adm.Owner, adm.Session)
	return map[string]any{
		"torn_down":   true,
		"jobs_killed": killed,
		"destination": adm.Destination,
		"owner":       adm.Owner,
		"session":     adm.Session,
	}, nil
}
