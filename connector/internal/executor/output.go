package executor

import "sync"

// outputBuffer keeps a bounded prefix while accepting/draining every byte.
// Reads and writes may run concurrently for background jobs.
type outputBuffer struct {
	mu        sync.Mutex
	data      []byte
	truncated bool
}

func (b *outputBuffer) Write(p []byte) (int, error) {
	b.mu.Lock()
	defer b.mu.Unlock()
	n := len(p)
	remaining := maxOutputBytes - len(b.data)
	if len(p) > remaining {
		p = p[:remaining]
		b.truncated = true
	}
	b.data = append(b.data, p...)
	return n, nil
}

func (b *outputBuffer) String() string {
	b.mu.Lock()
	defer b.mu.Unlock()
	s := string(b.data)
	if b.truncated {
		s += "\n[truncated]"
	}
	return s
}
