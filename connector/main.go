// tomo-connector — Go agent that opens an outbound WebSocket to Tomo
// and runs bash/file RPC for tunnel workplaces.
//
//	tomo-connector pair --code <CODE> --server https://host:port
//	tomo-connector run
//	tomo-connector status
//	tomo-connector logout
package main

import (
	"fmt"
	"os"
	"sync"
)

// Serializes websocket writes from heartbeat + RPC handlers.
var writeMu sync.Mutex

func main() {
	if len(os.Args) < 2 {
		printUsage()
		os.Exit(2)
	}
	cmd := os.Args[1]
	args := os.Args[2:]
	var err error
	switch cmd {
	case "pair":
		err = cmdPair(args)
	case "run":
		err = runWithState()
	case "status":
		err = printStatus()
	case "logout":
		err = clearState()
		if err == nil {
			fmt.Println("logged out — local state removed")
		}
	case "help", "-h", "--help":
		printUsage()
	default:
		fmt.Fprintf(os.Stderr, "unknown command: %s\n", cmd)
		printUsage()
		os.Exit(2)
	}
	if err != nil {
		fmt.Fprintf(os.Stderr, "error: %v\n", err)
		os.Exit(1)
	}
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
`, connectorVersion)
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
	return pairAndRun(server, code)
}
