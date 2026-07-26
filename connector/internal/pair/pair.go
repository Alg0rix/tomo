// Package pair implements HTTP pairing with the Tomo server.
package pair

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"runtime"
	"strings"

	"github.com/tomo-project/tomo/connector/internal/state"
	"github.com/tomo-project/tomo/connector/internal/version"
)

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
	payload := map[string]string{
		"pairing_code": strings.ToUpper(strings.TrimSpace(code)),
		"device_name":  hostname,
		"platform":     runtime.GOOS,
		"version":      version.Version,
	}
	body, _ := json.Marshal(payload)
	resp, err := http.Post(server+"/api/connector/pair", "application/json", bytes.NewReader(body))
	if err != nil {
		return fmt.Errorf("pair request failed: %w", err)
	}
	defer resp.Body.Close()
	raw, _ := io.ReadAll(resp.Body)
	if resp.StatusCode != 200 {
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
		return fmt.Errorf("invalid pair response: %w", err)
	}
	if !result.OK {
		return fmt.Errorf("pairing failed: %s", result.Error)
	}
	token := result.ConnectorToken
	if token == "" {
		token = result.Token
	}
	if token == "" || result.WorkplaceID == "" {
		return fmt.Errorf("pair response missing token or workplace_id")
	}
	st := &state.State{
		ServerURL:   server,
		WorkplaceID: result.WorkplaceID,
		Token:       token,
	}
	if err := state.Save(st); err != nil {
		return fmt.Errorf("save state: %w", err)
	}
	name := result.WorkplaceName
	if name == "" {
		name = result.WorkplaceID
	}
	fmt.Printf("Paired successfully!\n")
	fmt.Printf("  Workplace: %s (%s)\n", name, result.WorkplaceID)
	if len(token) > 8 {
		fmt.Printf("  Token:     %s…\n", token[:8])
	}
	fmt.Println("\nRun 'tomo-connector run' to connect.")
	return nil
}
