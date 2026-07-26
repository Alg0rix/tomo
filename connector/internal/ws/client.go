// Package ws is the outbound WebSocket client + RPC message loop.
package ws

import (
	"encoding/json"
	"fmt"
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
	"github.com/tomo-project/tomo/connector/internal/clog"
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
		clog.Error("run.not_paired", err)
		return fmt.Errorf("not paired — run: tomo-connector pair --code <CODE> --server <URL>\n(%v)", err)
	}
	clog.Event("run.start",
		"server", st.ServerURL,
		"workplace_id", st.WorkplaceID,
		"token", clog.MaskToken(st.Token),
		"version", version.Version,
	)
	return runReconnectLoop(st)
}

func runReconnectLoop(st *state.State) error {
	backoff := 1.0
	const maxBackoff = 30.0
	attempt := 0
	for {
		attempt++
		connectedAt := time.Now()
		clog.Event("ws.connect.attempt",
			"n", attempt,
			"server", st.ServerURL,
			"workplace_id", st.WorkplaceID,
		)
		err := connectBearer(st)
		if err != nil {
			jitter := 1.0 + (0.4*float64(time.Now().UnixNano()%100)/100.0 - 0.2)
			wait := time.Duration(backoff*jitter*1000) * time.Millisecond
			if wait > 30*time.Second {
				wait = 30 * time.Second
			}
			clog.Error("ws.disconnected", err,
				"attempt", attempt,
				"retry_in_s", fmt.Sprintf("%.1f", wait.Seconds()),
				"uptime_s", fmt.Sprintf("%.1f", time.Since(connectedAt).Seconds()),
			)
			time.Sleep(wait)
			backoff = math.Min(backoff*2, maxBackoff)
			continue
		}
		if time.Since(connectedAt) > 10*time.Second {
			backoff = 1.0
		}
		clog.Event("ws.session.ended_clean",
			"uptime_s", fmt.Sprintf("%.1f", time.Since(connectedAt).Seconds()),
		)
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

// localIPv4 returns a best-effort non-loopback IPv4 for this machine (LAN IP).
func localIPv4() string {
	ifaces, err := net.Interfaces()
	if err != nil {
		return ""
	}
	var fallback string
	for _, iface := range ifaces {
		if iface.Flags&net.FlagUp == 0 || iface.Flags&net.FlagLoopback != 0 {
			continue
		}
		addrs, err := iface.Addrs()
		if err != nil {
			continue
		}
		for _, a := range addrs {
			var ip net.IP
			switch v := a.(type) {
			case *net.IPNet:
				ip = v.IP
			case *net.IPAddr:
				ip = v.IP
			}
			if ip == nil || ip.IsLoopback() {
				continue
			}
			ip4 := ip.To4()
			if ip4 == nil {
				continue
			}
			// Prefer RFC1918 private ranges for "device local" display.
			if ip4[0] == 10 || (ip4[0] == 172 && ip4[1] >= 16 && ip4[1] <= 31) || (ip4[0] == 192 && ip4[1] == 168) {
				return ip4.String()
			}
			if fallback == "" {
				fallback = ip4.String()
			}
		}
	}
	return fallback
}

func connectBearer(st *state.State) error {
	wsURL, err := toWSURL(st.ServerURL)
	if err != nil {
		return err
	}
	lip := localIPv4()
	clog.Event("ws.dial", "url", wsURL, "device", hostname(), "platform", runtime.GOOS, "local_ip", lip)
	header := http.Header{}
	header.Set("Authorization", "Bearer "+st.Token)
	header.Set("User-Agent", "tomo-connector/"+version.Version)
	header.Set("X-Device-Name", hostname())
	header.Set("X-Platform", runtime.GOOS)
	header.Set("X-Tomo-Connector-Version", version.Version)
	header.Set("X-Tomo-Caps", "idempotent-replay")
	if lip != "" {
		header.Set("X-Tomo-Local-IP", lip)
		header.Set("X-Device-IP", lip)
	}

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
		code := 0
		if resp != nil {
			code = resp.StatusCode
		}
		return fmt.Errorf("dial: %w (HTTP %d)", err, code)
	}
	defer conn.Close()
	conn.SetReadLimit(512 * 1024)
	clog.Event("ws.connected",
		"workplace_id", st.WorkplaceID,
		"url", wsURL,
	)
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
				err := conn.WriteJSON(envelope{V: 1, Type: "ping"})
				writeMu.Unlock()
				if err != nil {
					clog.Error("ws.ping.send_fail", err)
				} else {
					clog.Event("ws.out", "type", "ping")
				}
			}
		}
	}()

	for {
		_, data, err := conn.ReadMessage()
		if err != nil {
			clog.Error("ws.read_error", err)
			return err
		}
		clog.Event("ws.in.raw", "bytes", len(data), "preview", clog.Truncate(string(data), 300))
		var msg envelope
		if err := json.Unmarshal(data, &msg); err != nil {
			clog.Error("ws.in.bad_json", err, "preview", clog.Truncate(string(data), 200))
			continue
		}
		clog.Event("ws.in",
			"type", msg.Type,
			"id", msg.ID,
			"method", msg.Method,
			"workplace_id", msg.WorkplaceID,
			"message", clog.Truncate(msg.Message, 200),
			"params", clog.JSON(msg.Params, 400),
		)
		switch msg.Type {
		case "pong", "heartbeat_ack":
			clog.Event("ws.liveness", "type", msg.Type)
		case "hello_ok":
			if msg.WorkplaceID != "" {
				st.WorkplaceID = msg.WorkplaceID
				if err := state.Save(st); err != nil {
					clog.Error("ws.hello_ok.save_fail", err)
				}
			}
			clog.Event("ws.hello_ok", "workplace_id", st.WorkplaceID)
		case "rpc_request":
			go handleRPCRequest(conn, msg)
		case "error":
			clog.Event("ws.server_error", "message", msg.Message)
		default:
			clog.Event("ws.in.unknown_type", "type", msg.Type)
		}
	}
}

func handleRPCRequest(conn *websocket.Conn, msg envelope) {
	t0 := time.Now()
	clog.Event("rpc.request",
		"id", msg.ID,
		"method", msg.Method,
		"params", clog.JSON(msg.Params, 500),
	)
	out := executeCached(msg)
	elapsed := time.Since(t0)
	if out.OK {
		clog.Event("rpc.response",
			"id", out.ID,
			"method", msg.Method,
			"ok", true,
			"ms", elapsed.Milliseconds(),
			"result", clog.JSON(out.Result, 500),
		)
	} else {
		clog.Event("rpc.response",
			"id", out.ID,
			"method", msg.Method,
			"ok", false,
			"ms", elapsed.Milliseconds(),
			"error", out.Error,
		)
	}
	writeMu.Lock()
	defer writeMu.Unlock()
	if werr := conn.WriteJSON(out); werr != nil {
		clog.Error("rpc.write_fail", werr, "id", out.ID, "method", msg.Method)
	} else {
		clog.Event("ws.out", "type", "rpc_response", "id", out.ID, "ok", out.OK)
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
		clog.Event("rpc.no_id", "method", msg.Method)
		return run()
	}

	rpcMu.Lock()
	if existing, ok := rpcCache[id]; ok {
		rpcMu.Unlock()
		clog.Event("rpc.cache_hit", "id", id, "method", msg.Method)
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
		clog.Event("rpc.cache_evict", "id", id)
	}()
	return out
}
