// Package state persists pairing credentials under ~/.tomo-connector.
package state

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
)

// State is persisted under ~/.tomo-connector/state.json (mode 0600).
type State struct {
	ServerURL   string `json:"server_url"`
	WorkplaceID string `json:"workplace_id"`
	Token       string `json:"token"`
}

// Home returns $TOMO_CONNECTOR_HOME or ~/.tomo-connector.
func Home() (string, error) {
	if v := os.Getenv("TOMO_CONNECTOR_HOME"); v != "" {
		return v, nil
	}
	home, err := os.UserHomeDir()
	if err != nil {
		return "", err
	}
	return filepath.Join(home, ".tomo-connector"), nil
}

func path() (string, error) {
	dir, err := Home()
	if err != nil {
		return "", err
	}
	return filepath.Join(dir, "state.json"), nil
}

// Load reads saved pairing state.
func Load() (*State, error) {
	p, err := path()
	if err != nil {
		return nil, err
	}
	data, err := os.ReadFile(p)
	if err != nil {
		return nil, err
	}
	var st State
	if err := json.Unmarshal(data, &st); err != nil {
		return nil, err
	}
	if st.ServerURL == "" || st.Token == "" {
		return nil, errors.New("incomplete connector state")
	}
	return &st, nil
}

// Save writes state with file mode 0600.
func Save(st *State) error {
	dir, err := Home()
	if err != nil {
		return err
	}
	if err := os.MkdirAll(dir, 0o700); err != nil {
		return err
	}
	p := filepath.Join(dir, "state.json")
	data, err := json.MarshalIndent(st, "", "  ")
	if err != nil {
		return err
	}
	tmp := p + ".tmp"
	if err := os.WriteFile(tmp, data, 0o600); err != nil {
		return err
	}
	return os.Rename(tmp, p)
}

// Clear removes saved state (logout).
func Clear() error {
	p, err := path()
	if err != nil {
		return err
	}
	err = os.Remove(p)
	if err != nil && !os.IsNotExist(err) {
		return err
	}
	return nil
}

// PrintStatus prints pair status to stdout.
func PrintStatus() error {
	st, err := Load()
	if err != nil {
		fmt.Println("status: not paired")
		fmt.Printf("  (%v)\n", err)
		return nil
	}
	fmt.Println("status: paired")
	fmt.Printf("  server:       %s\n", st.ServerURL)
	fmt.Printf("  workplace_id: %s\n", st.WorkplaceID)
	token := st.Token
	if len(token) > 8 {
		token = token[:4] + "…" + token[len(token)-4:]
	}
	fmt.Printf("  token:        %s\n", token)
	return nil
}
