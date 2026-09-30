//go:build !linux && !darwin && !windows

package executor

import (
	"os"
	"os/exec"
	"time"
)

func prepareProcess(cmd *exec.Cmd) { cmd.WaitDelay = 2 * time.Second }
func terminateProcess(cmd *exec.Cmd) error {
	if cmd.Process == nil {
		return os.ErrProcessDone
	}
	return cmd.Process.Kill()
}
