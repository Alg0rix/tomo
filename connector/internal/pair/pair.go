// Package pair implements HTTP pairing with the Tomo server.
package pair

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net"
	"net/http"
	"os"
	"runtime"
	"strings"

	"github.com/tomo-project/tomo/connector/internal/clog"
	"github.com/tomo-project/tomo/connector/internal/state"
	"github.com/tomo-project/tomo/connector/internal/version"
)

// localIPv4 is duplicated lightly from ws to avoid an import cycle.
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

// HTTP exchanges a short-lived code for a long-lived connector token and saves state.
func HTTP(server, code string) error {
	server = strings.TrimRight(strings.TrimSpace(server), "/")
	if server == "" {
		return fmt.Errorf("server URL is required")
	}
	hostname, _ := os.Hostname()
	if hostname == "" {
		hostname = "connector"
	}
	lip := localIPv4()
	payload := map[string]string{
		"pairing_code": strings.ToUpper(strings.TrimSpace(code)),
		"device_name":  hostname,
		"platform":     runtime.GOOS,
		"version":      version.Version,
		"local_ip":     lip,
		"device_ip":    lip,
	}
	clog.Event("pair.request",
		"server", server,
		"code", strings.ToUpper(strings.TrimSpace(code)),
		"device", hostname,
		"platform", runtime.GOOS,
		"version", version.Version,
		"local_ip", lip,
	)
	body, _ := json.Marshal(payload)
	resp, err := http.Post(server+"/api/connector/pair", "application/json", bytes.NewReader(body))
	if err != nil {
		clog.Error("pair.http_fail", err, "server", server)
		return fmt.Errorf("pair request failed: %w", err)
	}
	defer resp.Body.Close()
	raw, _ := io.ReadAll(resp.Body)
	clog.Event("pair.response",
		"http_status", resp.StatusCode,
		"body", clog.Truncate(string(raw), 300),
	)
	if resp.StatusCode != 200 {
		clog.Event("pair.failed", "http_status", resp.StatusCode, "body", clog.Truncate(string(raw), 200))
		return fmt.Errorf("pairing failed (HTTP %d): %s", resp.StatusCode, string(raw))
	}
	var result struct {
		OK             bool   `json:"ok"`
		ConnectorToken string `json:"connector_token"`
		Token          string `json:"token"`
		WorkplaceID    string `json:"workplace_id"`
		WorkplaceName  string `json:"workplace_name"`
		Error          string `json:"error"`
	}
	if err := json.Unmarshal(raw, &result); err != nil {
		clog.Error("pair.bad_json", err)
		return fmt.Errorf("invalid pair response: %w", err)
	}
	if !result.OK {
		clog.Event("pair.failed", "error", result.Error)
		return fmt.Errorf("pairing failed: %s", result.Error)
	}
	token := result.ConnectorToken
	if token == "" {
		token = result.Token
	}
	if token == "" || result.WorkplaceID == "" {
		clog.Event("pair.failed", "error", "missing token or workplace_id")
		return fmt.Errorf("pair response missing token or workplace_id")
	}
	st := &state.State{
		ServerURL:   server,
		WorkplaceID: result.WorkplaceID,
		Token:       token,
	}
	if err := state.Save(st); err != nil {
		clog.Error("pair.save_fail", err)
		return fmt.Errorf("save state: %w", err)
	}
	name := result.WorkplaceName
	if name == "" {
		name = result.WorkplaceID
	}
	clog.Event("pair.ok",
		"workplace_id", result.WorkplaceID,
		"workplace_name", name,
		"token", clog.MaskToken(token),
	)
	fmt.Printf("Paired successfully!\n")
	fmt.Printf("  Workplace: %s (%s)\n", name, result.WorkplaceID)
	if len(token) > 8 {
		fmt.Printf("  Token:     %s…\n", token[:8])
	}
	fmt.Println("\nRun 'tomo-connector run' to connect.")
	return nil
}
