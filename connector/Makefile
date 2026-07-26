.PHONY: build tidy clean test

BINARY := tomo-connector
CMD := ./cmd/tomo-connector

build:
	go build -trimpath -ldflags="-s -w" -o $(BINARY) $(CMD)

tidy:
	go mod tidy

clean:
	rm -f $(BINARY)

# No unit tests yet; smoke-compile packages.
test:
	go test ./...
