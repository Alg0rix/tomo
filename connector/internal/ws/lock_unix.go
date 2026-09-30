//go:build linux || darwin

package ws

import (
	"fmt"
	"os"
	"syscall"
)

func lockJournal(path string) (*os.File, error) {
	f, err := os.OpenFile(path, os.O_CREATE|os.O_RDWR, 0600)
	if err != nil {
		return nil, err
	}
	if err := syscall.Flock(int(f.Fd()), syscall.LOCK_EX|syscall.LOCK_NB); err != nil {
		_ = f.Close()
		return nil, fmt.Errorf("RPC journal is already in use: %w", err)
	}
	return f, nil
}
