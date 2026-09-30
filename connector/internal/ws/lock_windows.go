//go:build windows

package ws

import (
	"fmt"
	"os"
	"syscall"
)

func lockJournal(path string) (*os.File, error) {
	name, err := syscall.UTF16PtrFromString(path)
	if err != nil {
		return nil, err
	}
	// No sharing: the OS releases the exclusive handle when the process exits.
	handle, err := syscall.CreateFile(name, syscall.GENERIC_READ|syscall.GENERIC_WRITE, 0, nil, syscall.OPEN_ALWAYS, syscall.FILE_ATTRIBUTE_NORMAL, 0)
	if err != nil {
		return nil, fmt.Errorf("RPC journal is already in use: %w", err)
	}
	return os.NewFile(uintptr(handle), path), nil
}
