// Sandbox attestation for restricted remote execution.
//
// Restricted work requires an equivalent per-chat container boundary with
// the full-toolchain image at the destination. This connector makes no such
// claim by default: it advertises remote-sandbox-v1 only when the operator
// provisioned it, proven by a marker the image build bakes in
// (TOMO_CONNECTOR_SANDBOX_MARKER, default /etc/tomo-sandbox) whose content
// equals the pinned image digest in TOMO_CONNECTOR_SANDBOX_IMAGE.
//
// Deployment contract (see deployment/sandbox/README.md): run the connector
// inside the full-toolchain sandbox image with both values set to the
// image digest. A bare host connector never advertises the capability, so
// restricted execution refuses instead of running unconfined.
package executor

import (
	"os"
	"strings"
)

// DefaultSandboxMarker is baked into the sandbox image at build time.
const DefaultSandboxMarker = "/etc/tomo-sandbox"

// SandboxImage returns the operator-pinned destination image digest, or "".
func SandboxImage() string {
	return strings.TrimSpace(os.Getenv("TOMO_CONNECTOR_SANDBOX_IMAGE"))
}

// SandboxCapable reports whether this destination attests a per-chat
// container boundary for restricted work.
func SandboxCapable() bool {
	want := SandboxImage()
	if want == "" {
		return false
	}
	marker := strings.TrimSpace(os.Getenv("TOMO_CONNECTOR_SANDBOX_MARKER"))
	if marker == "" {
		marker = DefaultSandboxMarker
	}
	raw, err := os.ReadFile(marker)
	if err != nil {
		return false
	}
	return strings.TrimSpace(string(raw)) == want
}
