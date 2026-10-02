//go:build windows

package executor

import (
	"context"
	"os"
	"os/exec"
	"strconv"
	"time"
)

func prepareProcess(cmd *exec.Cmd) { cmd.WaitDelay = 2 * time.Second }

func terminateProcess(cmd *exec.Cmd) error {
	if cmd.Process == nil {
		return os.ErrProcessDone
	}
	// taskkill /T includes descendants on Windows; /F performs forced termination.
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	err := exec.CommandContext(ctx, "taskkill", "/PID", strconv.Itoa(cmd.Process.Pid), "/T", "/F").Run()
	if err != nil {
		return cmd.Process.Kill()
	}
	return nil
}

// Windows tree cleanup after a parent exits needs Job Objects. Refuse the
// supervised contract until that ownership primitive is implemented.
func backgroundJobContract() int                 { return 0 }
func cleanupBackgroundGroup(cmd *exec.Cmd) error { return terminateProcess(cmd) }
