package main

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

func connectorHome() (string, error) {
	if v := os.Getenv("TOMO_CONNECTOR_HOME"); v != "" {
		return v, nil
	}
	home, err := os.UserHomeDir()
	if err != nil {
		return "", err
	}
	return filepath.Join(home, ".tomo-connector"), nil
}

func statePath() (string, error) {
	dir, err := connectorHome()
	if err != nil {
		return "", err
	}
	return filepath.Join(dir, "state.json"), nil
}

func loadState() (*State, error) {
	path, err := statePath()
	if err != nil {
		return nil, err
	}
	data, err := os.ReadFile(path)
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

func saveState(st *State) error {
	dir, err := connectorHome()
	if err != nil {
		return err
	}
	if err := os.MkdirAll(dir, 0o700); err != nil {
		return err
	}
	path := filepath.Join(dir, "state.json")
	data, err := json.MarshalIndent(st, "", "  ")
	if err != nil {
		return err
	}
	tmp := path + ".tmp"
	if err := os.WriteFile(tmp, data, 0o600); err != nil {
		return err
	}
	return os.Rename(tmp, path)
}

func clearState() error {
	path, err := statePath()
	if err != nil {
		return err
	}
	err = os.Remove(path)
	if err != nil && !os.IsNotExist(err) {
		return err
	}
	return nil
}

func printStatus() error {
	st, err := loadState()
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
