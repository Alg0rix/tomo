// tomo-connector — outbound WebSocket agent for Tomo tunnel workplaces.
//
//	tomo-connector pair --code <CODE> --server https://host:port
//	tomo-connector run
//	tomo-connector status
//	tomo-connector logout
package main

import (
	"fmt"
	"os"

	"github.com/tomo-project/tomo/connector/internal/clog"
	"github.com/tomo-project/tomo/connector/internal/pair"
	"github.com/tomo-project/tomo/connector/internal/state"
	"github.com/tomo-project/tomo/connector/internal/version"
	"github.com/tomo-project/tomo/connector/internal/ws"
)

func main() {
	clog.Setup()
	if len(os.Args) < 2 {
		printUsage()
		os.Exit(2)
	}
	cmd := os.Args[1]
	args := os.Args[2:]
	clog.Event("cli.command", "cmd", cmd, "version", version.Version)
	var err error
	switch cmd {
	case "pair":
		err = cmdPair(args)
	case "run":
		err = ws.Run()
	case "status":
		err = state.PrintStatus()
	case "logout":
		err = state.Clear()
		if err == nil {
			clog.Event("cli.logout")
			fmt.Println("logged out — local state removed")
		}
	case "help", "-h", "--help":
		printUsage()
	default:
		clog.Event("cli.unknown_command", "cmd", cmd)
		fmt.Fprintf(os.Stderr, "unknown command: %s\n", cmd)
		printUsage()
		os.Exit(2)
	}
	if err != nil {
		clog.Error("cli.exit_error", err, "cmd", cmd)
		fmt.Fprintf(os.Stderr, "error: %v\n", err)
		os.Exit(1)
	}
	clog.Event("cli.exit_ok", "cmd", cmd)
}

func printUsage() {
	fmt.Fprintf(os.Stderr, `tomo-connector %s — Tomo workplace tunnel agent

Usage:
  tomo-connector pair --code <CODE> --server <URL>
  tomo-connector run
  tomo-connector status
  tomo-connector logout

Environment:
  TOMO_CONNECTOR_HOME   state directory (default ~/.tomo-connector)
  TOMO_CONNECTOR_ROOT   jail root for bash/files (default $HOME/.tomo-connector/work)
  TOMO_CONNECTOR_PAIR_AND_RUN=1   after pair, start run immediately
`, version.Version)
}

func cmdPair(args []string) error {
	var code, server string
	for i := 0; i < len(args); i++ {
		switch args[i] {
		case "--code", "-c":
			if i+1 >= len(args) {
				return fmt.Errorf("--code requires a value")
			}
			i++
			code = args[i]
		case "--server", "-s":
			if i+1 >= len(args) {
				return fmt.Errorf("--server requires a value")
			}
			i++
			server = args[i]
		default:
			return fmt.Errorf("unknown flag: %s", args[i])
		}
	}
	if code == "" || server == "" {
		return fmt.Errorf("usage: tomo-connector pair --code <CODE> --server <URL>")
	}
	if err := pair.HTTP(server, code); err != nil {
		return err
	}
	if os.Getenv("TOMO_CONNECTOR_PAIR_AND_RUN") == "1" {
		return ws.Run()
	}
	return nil
}
