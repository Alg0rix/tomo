package ws

// Loopback-only bridge for the existing Tomo CLI. Only broker metadata/results
// go through this bridge; private consumer inputs travel in internal WS RPCs.
import (
	"fmt"
	"io"
	"net"
	"net/http"
	"net/url"
	"path"
	"strings"
	"time"
)

func startBroker(server string) (*http.Server, string, error) {
	base, err := url.Parse(server)
	if err != nil {
		return nil, "", fmt.Errorf("invalid broker server")
	}
	if base.Scheme == "wss" {
		base.Scheme = "https"
	}
	if base.Scheme == "ws" {
		base.Scheme = "http"
	}
	host := base.Hostname()
	ip := net.ParseIP(host)
	if base.Scheme != "https" && !(base.Scheme == "http" && (host == "localhost" || (ip != nil && ip.IsLoopback()))) {
		return nil, "", nil // Do not enable secret transport on plaintext public connections.
	}
	if base.User != nil || base.RawQuery != "" || base.Fragment != "" {
		return nil, "", fmt.Errorf("invalid broker server")
	}
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		return nil, "", err
	}
	transport := http.DefaultTransport.(*http.Transport).Clone()
	// Match the WS client's backend proxy routing; TLS still verifies the server.
	transport.Proxy = http.ProxyFromEnvironment
	client := &http.Client{Transport: transport, Timeout: 65 * time.Second, CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}
	handler := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if path.Clean(r.URL.Path) != r.URL.Path || r.URL.RawQuery != "" ||
			(r.Method != "GET" && r.Method != "POST" && r.Method != "DELETE") ||
			!(strings.HasPrefix(r.URL.Path, "/api/secret-broker/") || strings.HasPrefix(r.URL.Path, "/api/connection-broker/")) ||
			!strings.HasPrefix(r.Header.Get("Authorization"), "Bearer ") {
			http.Error(w, `{"detail":"Broker request refused"}`, 403)
			return
		}
		raw, err := io.ReadAll(io.LimitReader(r.Body, 1_050_001))
		if err != nil || len(raw) > 1_050_000 {
			http.Error(w, `{"detail":"Broker request too large"}`, 413)
			return
		}
		request, err := http.NewRequestWithContext(r.Context(), r.Method, strings.TrimRight(base.String(), "/")+r.URL.RequestURI(), strings.NewReader(string(raw)))
		if err != nil {
			http.Error(w, `{"detail":"Broker request failed"}`, 502)
			return
		}
		request.Header.Set("Authorization", r.Header.Get("Authorization"))
		request.Header.Set("Content-Type", "application/json")
		response, err := client.Do(request)
		if err != nil {
			http.Error(w, `{"detail":"Could not reach the Tomo broker"}`, 502)
			return
		}
		defer response.Body.Close()
		body, err := io.ReadAll(io.LimitReader(response.Body, 16<<20+1))
		if err != nil || len(body) > 16<<20 || response.StatusCode >= 300 && response.StatusCode < 400 {
			http.Error(w, `{"detail":"Broker response refused"}`, 502)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(response.StatusCode)
		_, _ = w.Write(body)
	})
	srv := &http.Server{Handler: handler, ReadHeaderTimeout: 5 * time.Second, ReadTimeout: 10 * time.Second, WriteTimeout: 70 * time.Second, MaxHeaderBytes: 8192}
	go func() { _ = srv.Serve(listener) }()
	return srv, "http://" + listener.Addr().String(), nil
}
