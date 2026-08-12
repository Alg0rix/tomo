// Package service installs and controls a systemd unit for tomo-connector.
//
// Non-root installs use a systemd --user unit under ~/.config/systemd/user.
// Root installs use a system unit under /etc/systemd/system (user sessions
// are not available for uid 0 on most hosts, so --user cannot start).
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

// systemUnitPath is where root installs the unit.
const systemUnitPath = "/etc/systemd/system/" + UnitName

// systemBinPath is the default binary path for root installs.
const systemBinPath = "/usr/local/bin/tomo-connector"

// UseSystemdSystem reports whether we should install/control a system unit
// (root) instead of a per-user unit.
func UseSystemdSystem() bool {
	return os.Geteuid() == 0
}

func homeDir() (string, error) {
	return os.UserHomeDir()
}

func connectorHome() (string, error) {
	if v := os.Getenv("TOMO_CONNECTOR_HOME"); v != "" {
		return v, nil
	}
	home, err := homeDir()
	if err != nil {
		return "", err
	}
	return filepath.Join(home, ".tomo-connector"), nil
}

func unitPath() (string, error) {
	if UseSystemdSystem() {
		return systemUnitPath, nil
	}
	cfg := os.Getenv("XDG_CONFIG_HOME")
	if cfg == "" {
		home, err := homeDir()
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
	if UseSystemdSystem() {
		return systemBinPath, nil
	}
	home, err := homeDir()
	if err != nil {
		return "", err
	}
	return filepath.Join(home, ".local", "bin", "tomo-connector"), nil
}

// UnitTextFor builds the unit file for the given binary and state home.
// When system is true, paths are absolute and WantedBy=multi-user.target.
func UnitTextFor(bin, connHome string, system bool) string {
	bin = strings.TrimSpace(bin)
	connHome = strings.TrimSpace(connHome)
	if system {
		return fmt.Sprintf(`[Unit]
Description=Tomo Connector
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=%s run
Restart=always
RestartSec=5
Environment=TOMO_CONNECTOR_HOME=%s
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
`, bin, connHome)
	}
	// User unit: %h expands to the invoking user's home at runtime.
	return `[Unit]
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
}

// UnitText is the legacy user-unit template (kept for deploy/ scripts).
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

func systemctlCmd(args ...string) *exec.Cmd {
	if UseSystemdSystem() {
		return exec.Command("systemctl", args...)
	}
	return exec.Command("systemctl", append([]string{"--user"}, args...)...)
}

func runSystemctl(args ...string) error {
	cmd := systemctlCmd(args...)
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

// Install copies this binary, writes the unit (user or system), and enables it.
func Install(noStart bool) error {
	system := UseSystemdSystem()
	mode := "user"
	if system {
		mode = "system"
	}
	fmt.Printf("→ systemd mode: %s\n", mode)

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

	connHome, err := connectorHome()
	if err != nil {
		return err
	}
	up, err := unitPath()
	if err != nil {
		return err
	}
	text := UnitTextFor(dest, connHome, system)
	// User unit still uses %h for the default binary path when not overridden.
	if !system {
		if os.Getenv("TOMO_CONNECTOR_BIN") != "" {
			// Custom bin path: write absolute ExecStart into the user unit.
			text = UnitTextFor(dest, connHome, true)
			// But keep WantedBy=default.target for user units.
			text = strings.Replace(text, "WantedBy=multi-user.target", "WantedBy=default.target", 1)
		} else {
			text = UnitText
		}
	}
	fmt.Printf("→ Writing unit → %s\n", up)
	if err := os.MkdirAll(filepath.Dir(up), 0o755); err != nil {
		return err
	}
	if err := os.WriteFile(up, []byte(text), 0o644); err != nil {
		return err
	}

	if _, err := exec.LookPath("systemctl"); err != nil {
		return fmt.Errorf("systemctl not found — unit written, enable manually: %s", up)
	}
	if err := runSystemctl("daemon-reload"); err != nil {
		if system {
			return fmt.Errorf("daemon-reload: %w", err)
		}
		return fmt.Errorf("daemon-reload (user): %w\n"+
			"hint: as root use system install (this binary will pick system mode when euid=0);\n"+
			"as a normal user ensure a login session exists, or run: loginctl enable-linger $USER", err)
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

	if system {
		fmt.Println("Tip: system unit — survives reboot (multi-user.target)")
	} else {
		fmt.Println("Tip: loginctl enable-linger $USER  # keep running after logout")
	}
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
	// Also clean the other mode's unit if present (root may have leftover user unit).
	if UseSystemdSystem() {
		if home, err := homeDir(); err == nil {
			legacy := filepath.Join(home, ".config", "systemd", "user", UnitName)
			_ = os.Remove(legacy)
		}
	} else if os.Geteuid() != 0 {
		// Non-root cannot remove /etc unit; ignore.
	}
	if _, err := exec.LookPath("systemctl"); err == nil {
		_ = runSystemctl("daemon-reload")
	}
	fmt.Printf("✓ Removed %s (binary and connector home kept)\n", up)
	return nil
}

// Action runs systemctl [ --user ] <action> tomo-connector.service
func Action(action string) error {
	switch action {
	case "start", "stop", "restart", "status", "enable", "disable":
	default:
		return fmt.Errorf("unknown service action: %s", action)
	}
	cmd := systemctlCmd(action, UnitName)
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
tomo-connector service — systemd unit (user or system)

  Non-root: systemd --user unit in ~/.config/systemd/user
  Root:     system unit in /etc/systemd/system (binary → /usr/local/bin)

  tomo-connector service install [--no-start]
  tomo-connector service uninstall
  tomo-connector service start|stop|restart|status|enable|disable
`) + "\n"
}
