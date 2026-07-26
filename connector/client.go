package main

import (
	"encoding/json"
	"fmt"
	"log"
	"net/http"
	"net/url"
	"os"
	"strings"
	"time"

	"github.com/gorilla/websocket"
)

const connectorVersion = "0.1.0"

type envelope struct {
	V      int            `json:"v"`
	Type   string         `json:"type"`
	Code   string         `json:"code,omitempty"`
	Token  string         `json:"token,omitempty"`
	ID     string         `json:"id,omitempty"`
	Method string         `json:"method,omitempty"`
	Params map[string]any `json:"params,omitempty"`
	OK     bool           `json:"ok,omitempty"`
	Result any            `json:"result,omitempty"`
	Error  string         `json:"error,omitempty"`
	Message string        `json:"message,omitempty"`
	WorkplaceID string    `json:"workplace_id,omitempty"`
	Hostname string       `json:"hostname,omitempty"`
	Version  string       `json:"version,omitempty"`
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
		// ok
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

func dial(wsURL string) (*websocket.Conn, error) {
	dialer := websocket.Dialer{
		HandshakeTimeout: 15 * time.Second,
		Proxy:            http.ProxyFromEnvironment,
	}
	conn, _, err := dialer.Dial(wsURL, nil)
	return conn, err
}

func pairAndRun(server, code string) error {
	wsURL, err := toWSURL(server)
	if err != nil {
		return err
	}
	conn, err := dial(wsURL)
	if err != nil {
		return fmt.Errorf("dial: %w", err)
	}
	defer conn.Close()

	hello := envelope{
		V:        1,
		Type:     "pair",
		Code:     strings.TrimSpace(code),
		Hostname: hostname(),
		Version:  connectorVersion,
	}
	if err := conn.WriteJSON(hello); err != nil {
		return err
	}
	var resp envelope
	if err := conn.ReadJSON(&resp); err != nil {
		return err
	}
	if resp.Type == "error" {
		return fmt.Errorf("pair failed: %s", resp.Message)
	}
	if resp.Type != "pair_ok" || resp.Token == "" {
		return fmt.Errorf("unexpected pair response: %s", resp.Type)
	}
	st := &State{
		ServerURL:   strings.TrimRight(strings.TrimSpace(server), "/"),
		WorkplaceID: resp.WorkplaceID,
		Token:       resp.Token,
	}
	if err := saveState(st); err != nil {
		return fmt.Errorf("save state: %w", err)
	}
	fmt.Printf("paired workplace %s — state saved\n", st.WorkplaceID)
	// Stay on this connection and serve RPC.
	return serveLoop(conn, st)
}

func runWithState() error {
	st, err := loadState()
	if err != nil {
		return fmt.Errorf("not paired — run: tomo-connector pair --code <CODE> --server <URL>\n(%v)", err)
	}
	return runReconnectLoop(st)
}

func runReconnectLoop(st *State) error {
	backoff := time.Second
	const maxBackoff = 60 * time.Second
	for {
		err := connectHello(st)
		if err != nil {
			log.Printf("disconnected: %v — retry in %s", err, backoff)
			time.Sleep(backoff)
			backoff *= 2
			if backoff > maxBackoff {
				backoff = maxBackoff
			}
			continue
		}
		// clean exit
		return nil
	}
}

func connectHello(st *State) error {
	wsURL, err := toWSURL(st.ServerURL)
	if err != nil {
		return err
	}
	conn, err := dial(wsURL)
	if err != nil {
		return fmt.Errorf("dial: %w", err)
	}
	defer conn.Close()

	msg := envelope{
		V:        1,
		Type:     "hello",
		Token:    st.Token,
		Hostname: hostname(),
		Version:  connectorVersion,
	}
	if err := conn.WriteJSON(msg); err != nil {
		return err
	}
	var resp envelope
	if err := conn.ReadJSON(&resp); err != nil {
		return err
	}
	if resp.Type == "error" {
		return fmt.Errorf("hello failed: %s", resp.Message)
	}
	if resp.Type != "hello_ok" {
		return fmt.Errorf("unexpected hello response: %s", resp.Type)
	}
	if resp.WorkplaceID != "" {
		st.WorkplaceID = resp.WorkplaceID
		_ = saveState(st)
	}
	log.Printf("connected as workplace %s", st.WorkplaceID)
	backoffReset := serveLoop(conn, st)
	return backoffReset
}

func serveLoop(conn *websocket.Conn, st *State) error {
	// Heartbeat ticker.
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
				_ = conn.WriteJSON(envelope{V: 1, Type: "heartbeat"})
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
		case "heartbeat_ack":
			// ok
		case "rpc_request":
			go handleRPCRequest(conn, msg)
		case "error":
			log.Printf("server error: %s", msg.Message)
		default:
			// ignore
		}
	}
}

func handleRPCRequest(conn *websocket.Conn, msg envelope) {
	result, err := handleRPC(msg.Method, msg.Params)
	out := envelope{
		V:    1,
		Type: "rpc_response",
		ID:   msg.ID,
		OK:   err == nil,
	}
	if err != nil {
		out.Error = err.Error()
	} else {
		out.Result = result
	}
	// WriteJSON is not concurrency-safe; use a simple mutex via serialized writes.
	writeMu.Lock()
	defer writeMu.Unlock()
	if werr := conn.WriteJSON(out); werr != nil {
		log.Printf("rpc write failed: %v", werr)
	}
}
