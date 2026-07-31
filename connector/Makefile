.PHONY: build tidy clean test install-service

BINARY := tomo-connector
CMD := ./cmd/tomo-connector

build:
	go build -trimpath -ldflags="-s -w" -o $(BINARY) $(CMD)

tidy:
	go mod tidy

clean:
	rm -f $(BINARY)

# Build + install systemd --user unit (see scripts/install-service.sh).
install-service: build
	./$(BINARY) service install

# No unit tests yet; smoke-compile packages.
test:
	go test ./...
