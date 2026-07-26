// Package ws is the outbound WebSocket client + RPC message loop.
package ws

import (
	"encoding/json"
	"fmt"
	"log"
	"math"
	"net"
	"net/http"
	"net/url"
	"os"
	"runtime"
	"strings"
	"sync"
	"time"

	"github.com/gorilla/websocket"
	"github.com/tomo-project/tomo/connector/internal/executor"
	"github.com/tomo-project/tomo/connector/internal/state"
	"github.com/tomo-project/tomo/connector/internal/version"
)

const rpcCacheTTL = 5 * time.Minute

type envelope struct {
	V           int            `json:"v"`
	Type        string         `json:"type"`
	ID          string         `json:"id,omitempty"`
	Method      string         `json:"method,omitempty"`
	Params      map[string]any `json:"params,omitempty"`
	OK          bool           `json:"ok,omitempty"`
	Result      any            `json:"result,omitempty"`
	Error       string         `json:"error,omitempty"`
	Message     string         `json:"message,omitempty"`
	WorkplaceID string         `json:"workplace_id,omitempty"`
}

type inflight struct {
	done chan struct{}
	out  envelope
}

var (
	writeMu  sync.Mutex
	rpcMu    sync.Mutex
	rpcCache = map[string]*inflight{}
)

// Run loads saved state and reconnects forever with backoff.
func Run() error {
	st, err := state.Load()
	if err != nil {
		return fmt.Errorf("not paired — run: tomo-connector pair --code <CODE> --server <URL>\n(%v)", err)
	}
	return runReconnectLoop(st)
}

func runReconnectLoop(st *state.State) error {
	backoff := 1.0
	const maxBackoff = 30.0
	for {
		connectedAt := time.Now()
		err := connectBearer(st)
		if err != nil {
			jitter := 1.0 + (0.4*float64(time.Now().UnixNano()%100)/100.0 - 0.2)
			wait := time.Duration(backoff*jitter*1000) * time.Millisecond
			if wait > 30*time.Second {
				wait = 30 * time.Second
			}
			log.Printf("disconnected: %v — retry in %.1fs", err, wait.Seconds())
			time.Sleep(wait)
			backoff = math.Min(backoff*2, maxBackoff)
			continue
		}
		if time.Since(connectedAt) > 10*time.Second {
			backoff = 1.0
		}
		return nil
	}
}

func toWSURL(server string) (string, error) {
	server = strings.TrimRight(strings.TrimSpace(server), "/")
	if server == "" {
		return "", fmt.Errorf("server URL is required")
	}
	u, err := url.Parse(server)
	if err != nil {
		return "", err
	}
	switch u.Scheme {
	case "http":
		u.Scheme = "ws"
	case "https":
		u.Scheme = "wss"
	case "ws", "wss":
	default:
		return "", fmt.Errorf("unsupported scheme %q (use http/https)", u.Scheme)
	}
	u.Path = strings.TrimRight(u.Path, "/") + "/api/connector/ws"
	u.RawQuery = ""
	u.Fragment = ""
	return u.String(), nil
}

func hostname() string {
	h, err := os.Hostname()
	if err != nil || h == "" {
		return "connector"
	}
	return h
}

func connectBearer(st *state.State) error {
	wsURL, err := toWSURL(st.ServerURL)
	if err != nil {
		return err
	}
	header := http.Header{}
	header.Set("Authorization", "Bearer "+st.Token)
	header.Set("User-Agent", "tomo-connector/"+version.Version)
	header.Set("X-Device-Name", hostname())
	header.Set("X-Platform", runtime.GOOS)
	header.Set("X-Tomo-Connector-Version", version.Version)
	header.Set("X-Tomo-Caps", "idempotent-replay")

	dialer := websocket.Dialer{
		HandshakeTimeout: 30 * time.Second,
		Proxy:            http.ProxyFromEnvironment,
		NetDialContext: (&net.Dialer{
			Timeout:   30 * time.Second,
			KeepAlive: 30 * time.Second,
		}).DialContext,
	}
	conn, resp, err := dialer.Dial(wsURL, header)
	if err != nil {
		if resp != nil {
			return fmt.Errorf("dial: %w (HTTP %d)", err, resp.StatusCode)
		}
		return fmt.Errorf("dial: %w", err)
	}
	defer conn.Close()
	conn.SetReadLimit(512 * 1024)
	log.Printf("connected as workplace %s", st.WorkplaceID)
	return serveLoop(conn, st)
}

func serveLoop(conn *websocket.Conn, st *state.State) error {
	stop := make(chan struct{})
	defer close(stop)
	go func() {
		t := time.NewTicker(25 * time.Second)
		defer t.Stop()
		for {
			select {
			case <-stop:
				return
			case <-t.C:
				writeMu.Lock()
				_ = conn.WriteJSON(envelope{V: 1, Type: "ping"})
				writeMu.Unlock()
			}
		}
	}()

	for {
		_, data, err := conn.ReadMessage()
		if err != nil {
			return err
		}
		var msg envelope
		if err := json.Unmarshal(data, &msg); err != nil {
			log.Printf("bad message: %v", err)
			continue
		}
		switch msg.Type {
		case "pong", "heartbeat_ack":
		case "hello_ok":
			if msg.WorkplaceID != "" {
				st.WorkplaceID = msg.WorkplaceID
				_ = state.Save(st)
			}
		case "rpc_request":
			go handleRPCRequest(conn, msg)
		case "error":
			log.Printf("server error: %s", msg.Message)
		}
	}
}

func handleRPCRequest(conn *websocket.Conn, msg envelope) {
	out := executeCached(msg)
	writeMu.Lock()
	defer writeMu.Unlock()
	if werr := conn.WriteJSON(out); werr != nil {
		log.Printf("rpc write failed: %v", werr)
	}
}

func executeCached(msg envelope) envelope {
	id := msg.ID
	run := func() envelope {
		result, err := executor.Handle(msg.Method, msg.Params)
		out := envelope{V: 1, Type: "rpc_response", ID: id, OK: err == nil}
		if err != nil {
			out.Error = err.Error()
		} else {
			out.Result = result
		}
		return out
	}
	if id == "" {
		return run()
	}

	rpcMu.Lock()
	if existing, ok := rpcCache[id]; ok {
		rpcMu.Unlock()
		<-existing.done
		return existing.out
	}
	entry := &inflight{done: make(chan struct{})}
	rpcCache[id] = entry
	rpcMu.Unlock()

	out := run()
	entry.out = out
	close(entry.done)

	go func() {
		time.Sleep(rpcCacheTTL)
		rpcMu.Lock()
		delete(rpcCache, id)
		rpcMu.Unlock()
	}()
	return out
}
