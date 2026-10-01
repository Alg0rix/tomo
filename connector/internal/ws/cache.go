package ws

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"

	"github.com/tomo-project/tomo/connector/internal/executor"
)

const (
	rpcCacheTTL      = 15 * time.Minute
	maxCacheEntries  = 256
	maxCacheBytes    = 64 << 20
	maxResponseBytes = 12 << 20
)

type cacheRecord struct {
	Fingerprint string          `json:"fingerprint"`
	Expires     time.Time       `json:"expires"`
	Completed   bool            `json:"completed"`
	Response    json.RawMessage `json:"response,omitempty"`
}
type cacheEntry struct {
	record cacheRecord
	done   chan struct{}
}
type rpcStore struct {
	mu      sync.Mutex
	dir     string
	entries map[string]*cacheEntry
	bytes   int
	lock    *os.File
}

// Persist intent before executing, and results before acknowledging. A crash in
// between leaves an uncertain request which must never be executed automatically.
func openRPCStore(dir string) (*rpcStore, error) {
	if err := os.MkdirAll(dir, 0700); err != nil {
		return nil, err
	}
	if err := syncJournalDir(filepath.Dir(dir)); err != nil {
		return nil, err
	}
	lock, err := lockJournal(filepath.Join(dir, ".lock"))
	if err != nil {
		return nil, err
	}
	success := false
	defer func() {
		if !success {
			_ = lock.Close()
		}
	}()
	s := &rpcStore{dir: dir, entries: make(map[string]*cacheEntry), lock: lock}
	files, err := os.ReadDir(dir)
	if err != nil {
		return nil, err
	}
	for _, file := range files {
		if strings.HasPrefix(file.Name(), ".record-") {
			if err := os.Remove(filepath.Join(dir, file.Name())); err != nil {
				return nil, err
			}
			continue
		}
		if !strings.HasSuffix(file.Name(), ".json") {
			continue
		}
		info, err := file.Info()
		if err != nil {
			return nil, err
		}
		if info.Size() > maxResponseBytes+4096 {
			return nil, fmt.Errorf("RPC journal record exceeds size limit")
		}
		raw, err := os.ReadFile(filepath.Join(dir, file.Name()))
		if err != nil {
			return nil, err
		}
		var record cacheRecord
		if err := json.Unmarshal(raw, &record); err != nil || record.Fingerprint == "" || record.Expires.IsZero() {
			return nil, fmt.Errorf("invalid RPC journal record")
		}
		if time.Now().After(record.Expires) {
			if err := os.Remove(filepath.Join(dir, file.Name())); err != nil {
				return nil, err
			}
			continue
		}
		if record.Completed {
			var out envelope
			if len(record.Response) == 0 || json.Unmarshal(record.Response, &out) != nil {
				return nil, fmt.Errorf("invalid RPC journal response")
			}
		}
		entry := &cacheEntry{record: record, done: make(chan struct{})}
		close(entry.done)
		s.entries[strings.TrimSuffix(file.Name(), ".json")] = entry
		s.bytes += len(record.Response)
		if len(s.entries) > maxCacheEntries || s.bytes > maxCacheBytes {
			return nil, fmt.Errorf("RPC journal capacity exceeded")
		}
	}
	success = true
	return s, nil
}

func (s *rpcStore) close() {
	if s.lock != nil {
		_ = s.lock.Close()
	}
}

func hashBytes(raw []byte) string { sum := sha256.Sum256(raw); return hex.EncodeToString(sum[:]) }
func rpcError(id, message string) envelope {
	return envelope{V: 1, Type: "rpc_response", ID: id, Error: message}
}

func (s *rpcStore) save(key string, record cacheRecord) error {
	raw, err := json.Marshal(record)
	if err != nil {
		return err
	}
	f, err := os.CreateTemp(s.dir, ".record-*")
	if err != nil {
		return err
	}
	name := f.Name()
	defer os.Remove(name)
	if _, err = f.Write(raw); err == nil {
		err = f.Sync()
	}
	closeErr := f.Close()
	if err != nil {
		return err
	}
	if closeErr != nil {
		return closeErr
	}
	if err := os.Rename(name, filepath.Join(s.dir, key+".json")); err != nil {
		return err
	}
	return syncJournalDir(s.dir)
}

func (s *rpcStore) cleanupLocked(now time.Time) {
	for key, e := range s.entries {
		select {
		case <-e.done:
		default:
			continue
		}
		if now.Before(e.record.Expires) {
			continue
		}
		// If deletion fails, retain the record and refuse capacity rather than retrying.
		if err := os.Remove(filepath.Join(s.dir, key+".json")); err != nil && !os.IsNotExist(err) {
			continue
		}
		s.bytes -= len(e.record.Response)
		delete(s.entries, key)
	}
}

func (s *rpcStore) execute(msg envelope) envelope { return s.executeWithProgress(msg, nil) }

// executeWithProgress runs msg once; progress sees live output only on a fresh
// execution, never on a journal replay.
func (s *rpcStore) executeWithProgress(msg envelope, progress executor.Progress) envelope {
	if msg.ID == "" {
		return rpcError("", "request id is required")
	}
	if len(msg.ID) > 128 {
		return rpcError("", "request id exceeds 128 bytes")
	}
	if len(msg.Method) > 128 {
		return rpcError(msg.ID, "method exceeds 128 bytes")
	}
	if msg.V != 1 {
		return rpcError(msg.ID, "unsupported protocol version")
	}
	request, _ := json.Marshal(struct {
		Method string
		Params map[string]any
	}{msg.Method, msg.Params})
	fingerprint := hashBytes(request)
	key := hashBytes([]byte(msg.ID))
	s.mu.Lock()
	s.cleanupLocked(time.Now())
	if existing := s.entries[key]; existing != nil {
		if existing.record.Fingerprint != fingerprint {
			s.mu.Unlock()
			return rpcError(msg.ID, "request id reused with different method or parameters")
		}
		s.mu.Unlock()
		<-existing.done
		if !existing.record.Completed {
			return rpcError(msg.ID, "execution status uncertain after interruption; inspect effects before issuing a new request")
		}
		var out envelope
		_ = json.Unmarshal(existing.record.Response, &out)
		return out
	}
	if len(s.entries) >= maxCacheEntries || s.bytes >= maxCacheBytes-maxCacheEntries*1024 {
		s.mu.Unlock()
		return rpcError(msg.ID, "busy: RPC replay cache is full")
	}
	entry := &cacheEntry{record: cacheRecord{Fingerprint: fingerprint, Expires: time.Now().Add(rpcCacheTTL)}, done: make(chan struct{})}
	if err := s.save(key, entry.record); err != nil {
		s.mu.Unlock()
		return rpcError(msg.ID, "cannot persist RPC intent; request was not executed")
	}
	s.entries[key] = entry
	s.mu.Unlock()

	out := executeRPC(msg, progress)
	raw, err := json.Marshal(out)
	s.mu.Lock()
	defer s.mu.Unlock()
	if err != nil || len(raw) > maxResponseBytes || s.bytes+len(raw) > maxCacheBytes-maxCacheEntries*1024 {
		out = rpcError(msg.ID, "execution completed but result exceeds replay storage limit; inspect effects before issuing a new request")
		raw, _ = json.Marshal(out)
	}
	record := entry.record
	record.Completed = true
	record.Expires = time.Now().Add(rpcCacheTTL)
	record.Response = raw
	diskRecord := record
	if msg.Method == "secret_file_read" || msg.Method == "secret_http" {
		// Keep private replies only in bounded process memory. A restart leaves
		// an uncertain intent, rather than leaking a response or executing again.
		diskRecord.Completed = false
		diskRecord.Response = nil
	}
	if err := s.save(key, diskRecord); err != nil {
		out = rpcError(msg.ID, "execution status uncertain: could not persist result; inspect effects before issuing a new request")
	} else {
		entry.record = record
		s.bytes += len(raw)
	}
	close(entry.done)
	return out
}

func executeRPC(msg envelope, progress executor.Progress) (out envelope) {
	// A handler panic must not strand duplicate requests forever.
	out = rpcError(msg.ID, "execution status uncertain: handler panicked; inspect effects before issuing a new request")
	defer func() { _ = recover() }()
	params := msg.Params
	if msg.BrokerURL != "" && (msg.Method == "exec_bash" || msg.Method == "bash") {
		if token, ok := params["broker_token"].(string); ok && token != "" {
			// Runtime bridge address is not part of the replay fingerprint.
			params = make(map[string]any, len(msg.Params)+1)
			for key, value := range msg.Params {
				params[key] = value
			}
			env := make(map[string]any)
			if original, ok := params["env"].(map[string]any); ok {
				for k, v := range original {
					env[k] = v
				}
			}
			env["TOMO_BROKER_URL"] = msg.BrokerURL
			env["TOMO_BROKER_TOKEN"] = token
			params["env"] = env
		}
	}
	result, err := executor.HandleWithProgress(msg.Method, params, progress)
	if err != nil {
		return rpcError(msg.ID, err.Error())
	}
	return envelope{V: 1, Type: "rpc_response", ID: msg.ID, OK: true, Result: result}
}
