package executor

import (
	"sync"
	"time"
	"unicode/utf8"
)

// Progress receives live output chunks while a command runs.
type Progress func(chunk string)

const streamFlushInterval = 100 * time.Millisecond

// streamWriter batches stdout/stderr bytes and forwards them to a Progress
// callback on a short interval, never splitting a UTF-8 sequence. Total
// forwarded bytes are bounded by maxOutputBytes.
type streamWriter struct {
	mu      sync.Mutex
	fn      Progress
	pending []byte
	sent    int
	cut     bool
	stop    chan struct{}
	done    chan struct{}
}

func newStreamWriter(fn Progress) *streamWriter {
	s := &streamWriter{fn: fn, stop: make(chan struct{}), done: make(chan struct{})}
	go func() {
		defer close(s.done)
		t := time.NewTicker(streamFlushInterval)
		defer t.Stop()
		for {
			select {
			case <-s.stop:
				s.flush(true)
				return
			case <-t.C:
				s.flush(false)
			}
		}
	}()
	return s
}

func (s *streamWriter) Write(p []byte) (int, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	room := maxOutputBytes - s.sent - len(s.pending)
	if room <= 0 {
		s.cut = true
		return len(p), nil
	}
	if len(p) > room {
		s.pending = append(s.pending, p[:room]...)
		s.cut = true
	} else {
		s.pending = append(s.pending, p...)
	}
	return len(p), nil
}

func (s *streamWriter) flush(final bool) {
	s.mu.Lock()
	n := len(s.pending)
	if !final {
		// Hold back an incomplete trailing rune until the rest arrives.
		for i := 1; i <= utf8.UTFMax && i <= n; i++ {
			if utf8.RuneStart(s.pending[n-i]) {
				if !utf8.FullRune(s.pending[n-i:]) {
					n -= i
				}
				break
			}
		}
	}
	chunk := string(s.pending[:n])
	s.pending = append(s.pending[:0], s.pending[n:]...)
	s.sent += n
	if final && s.cut {
		chunk += "\n…[live output truncated]\n"
		s.cut = false
	}
	s.mu.Unlock()
	if chunk != "" {
		s.fn(chunk)
	}
}

// Close flushes remaining output and stops the flusher.
func (s *streamWriter) Close() {
	close(s.stop)
	<-s.done
}
