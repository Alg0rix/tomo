package ws

import (
	"strings"
	"sync"
	"time"

	"github.com/gorilla/websocket"
	"github.com/tomo-project/tomo/connector/internal/clog"
)

const (
	rpcWorkers   = 8
	rpcQueueSize = 32
)

type socketWriter struct {
	conn    *websocket.Conn
	mu      sync.Mutex
	timeout time.Duration
}

func (w *socketWriter) send(out envelope) error {
	w.mu.Lock()
	defer w.mu.Unlock()
	err := w.conn.SetWriteDeadline(time.Now().Add(w.timeout))
	if err == nil {
		err = w.conn.WriteJSON(out)
	}
	if err != nil {
		_ = w.conn.Close()
	}
	return err
}

type rpcTask struct {
	msg    envelope
	writer *socketWriter
}
type rpcDispatcher struct {
	store        *rpcStore
	queue        chan rpcTask
	privateQueue chan rpcTask
	brokerURL    string
	workers      sync.WaitGroup
}

func newRPCDispatcher(store *rpcStore) *rpcDispatcher {
	d := &rpcDispatcher{store: store, queue: make(chan rpcTask, rpcQueueSize), privateQueue: make(chan rpcTask, rpcQueueSize)}
	for i := 0; i < rpcWorkers+2; i++ {
		queue := d.queue
		if i >= rpcWorkers {
			queue = d.privateQueue
		} // A waiting bash must not starve its consumer.
		d.workers.Add(1)
		go func() {
			defer d.workers.Done()
			for task := range queue {
				t0 := time.Now()
				id, writer := task.msg.ID, task.writer
				out := d.store.executeWithProgress(task.msg, func(chunk string) {
					// Best effort: a dropped chunk only affects the live view, not the result.
					_ = writer.send(envelope{V: 1, Type: "rpc_progress", ID: id, Result: map[string]string{"data": chunk}})
				})
				// Never log payloads, command text, environment, or handler error contents.
				clog.Event("rpc.response", "id", out.ID, "method", task.msg.Method, "ok", out.OK, "ms", time.Since(t0).Milliseconds())
				if err := task.writer.send(out); err != nil {
					clog.Error("rpc.write_fail", err, "id", out.ID)
				}
			}
		}()
	}
	return d
}
func (d *rpcDispatcher) submit(writer *socketWriter, msg envelope) {
	queue := d.queue
	if strings.HasPrefix(msg.Method, "secret_") {
		queue = d.privateQueue
	}
	select {
	case queue <- rpcTask{msg: msg, writer: writer}:
	default:
		_ = writer.send(rpcError(msg.ID, "busy: RPC queue is full"))
	}
}
func (d *rpcDispatcher) close() { close(d.queue); close(d.privateQueue); d.workers.Wait() }
