// Package clog is a tiny structured logger for tomo-connector events.
//
// Format: 2006-01-02T15:04:05.000Z event=name key=value ...
// Sensitive values (tokens) are never logged in full.
package clog

import (
	"encoding/json"
	"fmt"
	"log"
	"os"
	"strings"
	"sync"
	"time"
)

var (
	mu     sync.Mutex
	logger = log.New(os.Stderr, "", 0)
)

// Setup configures the process logger (call once from main).
func Setup() {
	log.SetFlags(0)
	log.SetOutput(os.Stderr)
	logger = log.New(os.Stderr, "", 0)
	Event("logger.start", "pid", os.Getpid())
}

// Event logs a named event with alternating key, value pairs.
func Event(name string, kv ...any) {
	var b strings.Builder
	b.WriteString(time.Now().UTC().Format("2006-01-02T15:04:05.000Z"))
	b.WriteString(" event=")
	b.WriteString(name)
	for i := 0; i+1 < len(kv); i += 2 {
		b.WriteByte(' ')
		b.WriteString(fmt.Sprint(kv[i]))
		b.WriteByte('=')
		b.WriteString(formatVal(kv[i+1]))
	}
	if len(kv)%2 == 1 {
		b.WriteString(" _odd=")
		b.WriteString(formatVal(kv[len(kv)-1]))
	}
	mu.Lock()
	logger.Println(b.String())
	mu.Unlock()
}

// Error logs an event with err field.
func Error(name string, err error, kv ...any) {
	args := make([]any, 0, len(kv)+2)
	args = append(args, kv...)
	if err != nil {
		args = append(args, "err", err.Error())
	}
	Event(name, args...)
}

// MaskToken returns a short preview of a secret token.
func MaskToken(tok string) string {
	tok = strings.TrimSpace(tok)
	if tok == "" {
		return "(empty)"
	}
	if len(tok) <= 8 {
		return "****"
	}
	return tok[:4] + "…" + tok[len(tok)-4:]
}

// Truncate shortens large strings for log lines.
func Truncate(s string, max int) string {
	if max <= 0 {
		max = 200
	}
	s = strings.ReplaceAll(s, "\n", "\\n")
	if len(s) <= max {
		return s
	}
	return s[:max] + fmt.Sprintf("…(%d chars)", len(s))
}

// JSON compact-encodes v for logs (truncated).
func JSON(v any, max int) string {
	if v == nil {
		return "null"
	}
	b, err := json.Marshal(v)
	if err != nil {
		return Truncate(fmt.Sprint(v), max)
	}
	return Truncate(string(b), max)
}

func formatVal(v any) string {
	switch t := v.(type) {
	case string:
		if strings.ContainsAny(t, " \t\n=\"'") {
			return strconvQuote(t)
		}
		return t
	case error:
		return strconvQuote(t.Error())
	default:
		s := fmt.Sprint(t)
		if strings.ContainsAny(s, " \t\n=\"'") {
			return strconvQuote(s)
		}
		return s
	}
}

func strconvQuote(s string) string {
	b, _ := json.Marshal(s)
	return string(b)
}
