package service

import (
	"strings"
	"testing"
)

func TestUnitTextForSystem(t *testing.T) {
	text := UnitTextFor("/usr/local/bin/tomo-connector", "/root/.tomo-connector", true)
	if !strings.Contains(text, "ExecStart=/usr/local/bin/tomo-connector run") {
		t.Fatalf("missing absolute ExecStart:\n%s", text)
	}
	if !strings.Contains(text, "Environment=TOMO_CONNECTOR_HOME=/root/.tomo-connector") {
		t.Fatalf("missing home env:\n%s", text)
	}
	if !strings.Contains(text, "WantedBy=multi-user.target") {
		t.Fatalf("system unit must use multi-user.target:\n%s", text)
	}
	if strings.Contains(text, "%h") {
		t.Fatalf("system unit must not use %%h:\n%s", text)
	}
}

func TestUnitTextForUser(t *testing.T) {
	text := UnitTextFor("", "", false)
	if !strings.Contains(text, "ExecStart=%h/.local/bin/tomo-connector run") {
		t.Fatalf("missing user ExecStart:\n%s", text)
	}
	if !strings.Contains(text, "WantedBy=default.target") {
		t.Fatalf("user unit must use default.target:\n%s", text)
	}
	if strings.Contains(text, "multi-user.target") {
		t.Fatalf("user unit must not use multi-user.target:\n%s", text)
	}
}
