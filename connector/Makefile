.PHONY: build dist tidy clean test install-service

BINARY := tomo-connector
CMD := ./cmd/tomo-connector
DIST := dist
LDFLAGS := -s -w

build:
	go build -trimpath -ldflags="$(LDFLAGS)" -o $(BINARY) $(CMD)

# Cross-compile release binaries (matches .github/workflows/ci.yml).
dist:
	mkdir -p $(DIST)
	CGO_ENABLED=0 GOOS=linux   GOARCH=amd64 go build -trimpath -ldflags="$(LDFLAGS)" -o $(DIST)/$(BINARY)-linux-amd64          $(CMD)
	CGO_ENABLED=0 GOOS=linux   GOARCH=arm64 go build -trimpath -ldflags="$(LDFLAGS)" -o $(DIST)/$(BINARY)-linux-arm64          $(CMD)
	CGO_ENABLED=0 GOOS=darwin  GOARCH=amd64 go build -trimpath -ldflags="$(LDFLAGS)" -o $(DIST)/$(BINARY)-darwin-amd64         $(CMD)
	CGO_ENABLED=0 GOOS=darwin  GOARCH=arm64 go build -trimpath -ldflags="$(LDFLAGS)" -o $(DIST)/$(BINARY)-darwin-arm64         $(CMD)
	CGO_ENABLED=0 GOOS=windows GOARCH=amd64 go build -trimpath -ldflags="$(LDFLAGS)" -o $(DIST)/$(BINARY)-windows-amd64.exe    $(CMD)
	ls -lh $(DIST)

tidy:
	go mod tidy

clean:
	rm -f $(BINARY)
	rm -rf $(DIST)

# Build + install systemd --user unit (see scripts/install-service.sh).
install-service: build
	./$(BINARY) service install

# No unit tests yet; smoke-compile packages.
test:
	go test ./...
