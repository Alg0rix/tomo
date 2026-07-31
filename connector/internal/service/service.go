// Package service installs and controls a systemd --user unit for tomo-connector.
package service

import (
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"strings"

	"github.com/tomo-project/tomo/connector/internal/state"
)

const UnitName = "tomo-connector.service"

// UnitText is the systemd --user unit template (%h = user home).
const UnitText = `[Unit]
Description=Tomo Connector
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=%h/.local/bin/tomo-connector run
Restart=always
RestartSec=5
Environment=TOMO_CONNECTOR_HOME=%h/.tomo-connector
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=default.target
`

func unitPath() (string, error) {
	cfg := os.Getenv("XDG_CONFIG_HOME")
	if cfg == "" {
		home, err := os.UserHomeDir()
		if err != nil {
			return "", err
		}
		cfg = filepath.Join(home, ".config")
	}
	return filepath.Join(cfg, "systemd", "user", UnitName), nil
}

func binPath() (string, error) {
	if v := os.Getenv("TOMO_CONNECTOR_BIN"); v != "" {
		return v, nil
	}
	home, err := os.UserHomeDir()
	if err != nil {
		return "", err
	}
	return filepath.Join(home, ".local", "bin", "tomo-connector"), nil
}

func systemctlUser(args ...string) *exec.Cmd {
	return exec.Command("systemctl", append([]string{"--user"}, args...)...)
}

func runSystemctl(args ...string) error {
	cmd := systemctlUser(args...)
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr
	return cmd.Run()
}

func copyFile(src, dst string) error {
	in, err := os.Open(src)
	if err != nil {
		return err
	}
	defer in.Close()
	if err := os.MkdirAll(filepath.Dir(dst), 0o755); err != nil {
		return err
	}
	tmp := dst + ".tmp"
	out, err := os.OpenFile(tmp, os.O_CREATE|os.O_WRONLY|os.O_TRUNC, 0o755)
	if err != nil {
		return err
	}
	_, copyErr := io.Copy(out, in)
	closeErr := out.Close()
	if copyErr != nil {
		_ = os.Remove(tmp)
		return copyErr
	}
	if closeErr != nil {
		_ = os.Remove(tmp)
		return closeErr
	}
	return os.Rename(tmp, dst)
}

// Install copies this binary to ~/.local/bin, writes the user unit, and enables it.
func Install(noStart bool) error {
	exe, err := os.Executable()
	if err != nil {
		return fmt.Errorf("resolve executable: %w", err)
	}
	exe, err = filepath.EvalSymlinks(exe)
	if err != nil {
		return fmt.Errorf("resolve executable: %w", err)
	}
	dest, err := binPath()
	if err != nil {
		return err
	}
	fmt.Printf("→ Installing binary → %s\n", dest)
	if err := copyFile(exe, dest); err != nil {
		return fmt.Errorf("install binary: %w", err)
	}

	up, err := unitPath()
	if err != nil {
		return err
	}
	fmt.Printf("→ Writing unit → %s\n", up)
	if err := os.MkdirAll(filepath.Dir(up), 0o755); err != nil {
		return err
	}
	if err := os.WriteFile(up, []byte(UnitText), 0o644); err != nil {
		return err
	}

	if _, err := exec.LookPath("systemctl"); err != nil {
		return fmt.Errorf("systemctl not found — unit written, enable manually")
	}
	if err := runSystemctl("daemon-reload"); err != nil {
		return fmt.Errorf("daemon-reload: %w", err)
	}

	if noStart {
		if err := runSystemctl("enable", UnitName); err != nil {
			return fmt.Errorf("enable: %w", err)
		}
		fmt.Println("✓ Enabled (not started). Start with: tomo-connector service start")
	} else {
		if err := runSystemctl("enable", "--now", UnitName); err != nil {
			return fmt.Errorf("enable --now: %w", err)
		}
		fmt.Println("✓ Enabled and started")
	}

	if _, err := state.Load(); err != nil {
		fmt.Println("⚠ Not paired yet — run: tomo-connector pair --code <CODE> --server <URL>")
		fmt.Println("  Then: tomo-connector service restart")
	}

	fmt.Println("Tip: loginctl enable-linger $USER  # keep running after logout")
	return nil
}

// Uninstall stops/disables the unit and removes the unit file (keeps binary + pairing state).
func Uninstall() error {
	if _, err := exec.LookPath("systemctl"); err == nil {
		_ = runSystemctl("disable", "--now", UnitName)
	}
	up, err := unitPath()
	if err != nil {
		return err
	}
	if err := os.Remove(up); err != nil && !os.IsNotExist(err) {
		return err
	}
	if _, err := exec.LookPath("systemctl"); err == nil {
		_ = runSystemctl("daemon-reload")
	}
	fmt.Printf("✓ Removed %s (binary and ~/.tomo-connector kept)\n", up)
	return nil
}

// Action runs systemctl --user <action> tomo-connector.service
func Action(action string) error {
	switch action {
	case "start", "stop", "restart", "status", "enable", "disable":
	default:
		return fmt.Errorf("unknown service action: %s", action)
	}
	cmd := systemctlUser(action, UnitName)
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr
	err := cmd.Run()
	if action == "status" {
		// systemctl status returns non-zero when inactive; still useful output
		return nil
	}
	return err
}

// UsageHelp documents the service subcommand.
func UsageHelp() string {
	return strings.TrimSpace(`
tomo-connector service — systemd --user unit

  tomo-connector service install [--no-start]
  tomo-connector service uninstall
  tomo-connector service start|stop|restart|status|enable|disable
`) + "\n"
}
