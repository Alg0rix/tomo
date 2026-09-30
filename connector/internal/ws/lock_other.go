//go:build !linux && !darwin && !windows

package ws

import (
	"fmt"
	"os"
)

func lockJournal(string) (*os.File, error) {
	return nil, fmt.Errorf("exclusive RPC journal locking is unsupported on this platform")
}
