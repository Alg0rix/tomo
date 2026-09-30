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
	"path/filepath"
	"runtime"
	"strings"
	"time"

	"github.com/gorilla/websocket"
	"github.com/tomo-project/tomo/connector/internal/clog"
	"github.com/tomo-project/tomo/connector/internal/state"
	"github.com/tomo-project/tomo/connector/internal/version"
)

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

type heartbeatConfig struct{ interval, readTimeout, writeTimeout time.Duration }

var defaultHeartbeat = heartbeatConfig{25 * time.Second, 75 * time.Second, 10 * time.Second}

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
	home, err := state.Home()
	if err != nil {
		return err
	}
	base := filepath.Join(home, "rpc-journal")
	if err := os.MkdirAll(base, 0700); err != nil {
		return err
	}
	if err := syncJournalDir(home); err != nil {
		return err
	}
	scope := hashBytes([]byte(st.ServerURL + "\x00" + st.WorkplaceID + "\x00" + st.Token))
	store, err := openRPCStore(filepath.Join(base, scope))
	if err != nil {
		return fmt.Errorf("open RPC journal: %w", err)
	}
	defer store.close()
	dispatcher := newRPCDispatcher(store)
	defer dispatcher.close()
	return runReconnectLoop(st, dispatcher)
}

func runReconnectLoop(st *state.State, dispatcher *rpcDispatcher) error {
	backoff := 1.0
	const maxBackoff = 30.0
	attempt := 0
	for {
		attempt++
		clog.Event("ws.connect.attempt",
			"n", attempt,
			"server", st.ServerURL,
			"workplace_id", st.WorkplaceID,
		)
		uptime, err := connectBearer(st, dispatcher)
		if uptime > 10*time.Second {
			backoff = 1.0
		}
		if err != nil {
			jitter := 1.0 + (0.4*float64(time.Now().UnixNano()%100)/100.0 - 0.2)
			wait := time.Duration(backoff*jitter*1000) * time.Millisecond
			if wait > 30*time.Second {
				wait = 30 * time.Second
			}
			clog.Error("ws.disconnected", err,
				"attempt", attempt,
				"retry_in_s", fmt.Sprintf("%.1f", wait.Seconds()),
				"uptime_s", fmt.Sprintf("%.1f", uptime.Seconds()),
			)
			time.Sleep(wait)
			backoff = math.Min(backoff*2, maxBackoff)
			continue
		}
		clog.Event("ws.session.ended_clean",
			"uptime_s", fmt.Sprintf("%.1f", uptime.Seconds()),
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

func connectBearer(st *state.State, dispatcher *rpcDispatcher) (time.Duration, error) {
	wsURL, err := toWSURL(st.ServerURL)
	if err != nil {
		return 0, err
	}
	lip := localIPv4()
	clog.Event("ws.dial", "url", wsURL, "device", hostname(), "platform", runtime.GOOS, "local_ip", lip)
	header := http.Header{}
	header.Set("Authorization", "Bearer "+st.Token)
	header.Set("User-Agent", "tomo-connector/"+version.Version)
	header.Set("X-Device-Name", hostname())
	header.Set("X-Platform", runtime.GOOS)
	header.Set("X-Tomo-Connector-Version", version.Version)
	header.Set("X-Tomo-Caps", "idempotent-replay,exec-stream")
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
		return 0, fmt.Errorf("dial: %w (HTTP %d)", err, code)
	}
	defer conn.Close()
	conn.SetReadLimit(512 * 1024)
	clog.Event("ws.connected",
		"workplace_id", st.WorkplaceID,
		"url", wsURL,
	)
	connectedAt := time.Now()
	err = serveLoop(conn, st, dispatcher, defaultHeartbeat)
	return time.Since(connectedAt), err
}

func serveLoop(conn *websocket.Conn, st *state.State, dispatcher *rpcDispatcher, heartbeat heartbeatConfig) error {
	writer := &socketWriter{conn: conn, timeout: heartbeat.writeTimeout}
	if err := conn.SetReadDeadline(time.Now().Add(heartbeat.readTimeout)); err != nil {
		return err
	}
	stop := make(chan struct{})
	defer close(stop)
	go func() {
		t := time.NewTicker(heartbeat.interval)
		defer t.Stop()
		for {
			select {
			case <-stop:
				return
			case <-t.C:
				err := writer.send(envelope{V: 1, Type: "ping"})
				if err != nil {
					clog.Error("ws.ping.send_fail", err)
					return
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
		clog.Event("ws.in.raw", "bytes", len(data))
		var msg envelope
		if err := json.Unmarshal(data, &msg); err != nil {
			clog.Event("ws.in.bad_json", "bytes", len(data))
			continue
		}
		clog.Event("ws.in",
			"type", msg.Type,
			"id", msg.ID,
			"method", msg.Method,
			"workplace_id", msg.WorkplaceID,
		)
		switch msg.Type {
		case "pong", "heartbeat_ack":
			if err := conn.SetReadDeadline(time.Now().Add(heartbeat.readTimeout)); err != nil {
				return err
			}
			clog.Event("ws.liveness", "type", msg.Type)
		case "hello_ok":
			if err := conn.SetReadDeadline(time.Now().Add(heartbeat.readTimeout)); err != nil {
				return err
			}
			if msg.WorkplaceID != "" {
				st.WorkplaceID = msg.WorkplaceID
				if err := state.Save(st); err != nil {
					clog.Error("ws.hello_ok.save_fail", err)
				}
			}
			clog.Event("ws.hello_ok", "workplace_id", st.WorkplaceID)
		case "rpc_request":
			dispatcher.submit(writer, msg)
		case "error":
			clog.Event("ws.server_error")
		default:
			clog.Event("ws.in.unknown_type", "type", msg.Type)
		}
	}
}
