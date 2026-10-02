//go:build linux || darwin

package executor

import (
	"context"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"strconv"
	"strings"
	"syscall"
	"time"
)

func prepareProcess(cmd *exec.Cmd) {
	cmd.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}
	cmd.WaitDelay = 2 * time.Second
}

// Own the process group, including shell children that inherit output pipes.
func terminateProcess(cmd *exec.Cmd) error {
	if cmd.Process == nil {
		return os.ErrProcessDone
	}
	pid := cmd.Process.Pid
	err := syscall.Kill(-pid, syscall.SIGTERM)
	if errors.Is(err, syscall.ESRCH) {
		return os.ErrProcessDone
	}
	if err != nil {
		return err
	}
	// Escalate for children that ignore TERM, including pipe holders.
	time.Sleep(200 * time.Millisecond)
	err = syscall.Kill(-pid, syscall.SIGKILL)
	if errors.Is(err, syscall.ESRCH) {
		return nil
	}
	return err
}

func backgroundJobContract() int { return 1 }

// A command owns all live members of its group, even after the shell exits.
func cleanupBackgroundGroup(cmd *exec.Cmd) error {
	if err := terminateProcess(cmd); err != nil && !errors.Is(err, os.ErrProcessDone) {
		return err
	}
	deadline := time.Now().Add(time.Second)
	for {
		ctx, cancel := context.WithTimeout(context.Background(), 500*time.Millisecond)
		probe := exec.CommandContext(ctx, "ps", "-eo", "pgid=,stat=")
		probe.WaitDelay = time.Second
		rows, err := probe.Output()
		cancel()
		if err != nil {
			return err
		}
		alive := false
		for _, row := range strings.Split(string(rows), "\n") {
			fields := strings.Fields(row)
			if len(fields) != 2 {
				continue
			}
			group, err := strconv.Atoi(fields[0])
			if err == nil && group == cmd.Process.Pid && !strings.HasPrefix(fields[1], "Z") {
				alive = true
				break
			}
		}
		if !alive {
			return nil
		}
		if time.Now().After(deadline) {
			return fmt.Errorf("process group cleanup could not be confirmed")
		}
		time.Sleep(25 * time.Millisecond)
	}
}
