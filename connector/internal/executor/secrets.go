package executor

// Internal consumers. Values never appear in errors, progress or command text.
import (
	"bytes"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"
)

var secretFileLock sync.Mutex

const maxSecretFile = 1 << 20

func secretDeadline(params map[string]any) error {
	expires, ok := asFloat(params["expires_at"])
	if !ok || float64(time.Now().UnixNano())/1e9 >= expires {
		return fmt.Errorf("private operation expired")
	}
	return nil
}

func secretPath(params map[string]any, adm *Admission, write bool) (string, error) {
	if adm == nil {
		return "", fmt.Errorf("execution admission is required")
	}
	// Credential-merged files land in the admitted destination scope, never
	// at coordinator-side paths.
	target, err := adm.AuthorizePath(paramString(params, "path"), write)
	if err != nil {
		return "", fmt.Errorf("invalid private target: %w", err)
	}
	parent, err := filepath.EvalSymlinks(filepath.Dir(target))
	if err != nil {
		return "", fmt.Errorf("private target parent unavailable")
	}
	target = filepath.Join(parent, filepath.Base(target))
	if info, err := os.Lstat(target); err == nil {
		if !info.Mode().IsRegular() {
			return "", fmt.Errorf("private target must be a regular file")
		}
	} else if !os.IsNotExist(err) {
		return "", fmt.Errorf("private target unavailable")
	}
	return target, nil
}

func privateFileBytes(path string) ([]byte, string, error) {
	f, err := os.Open(path)
	if os.IsNotExist(err) {
		return nil, "missing", nil
	}
	if err != nil {
		return nil, "", fmt.Errorf("private file unavailable")
	}
	defer f.Close()
	data, err := io.ReadAll(io.LimitReader(f, maxSecretFile+1))
	if err != nil || len(data) > maxSecretFile {
		return nil, "", fmt.Errorf("private file exceeds limits or is unavailable")
	}
	hash := sha256.Sum256(data)
	return data, hex.EncodeToString(hash[:]), nil
}

func secretFileRead(params map[string]any, adm *Admission) (any, error) {
	if adm == nil {
		return nil, fmt.Errorf("execution admission is required")
	}
	if err := secretDeadline(params); err != nil {
		return nil, err
	}
	secretFileLock.Lock()
	defer secretFileLock.Unlock()
	path, err := secretPath(params, adm, false)
	if err != nil {
		return nil, err
	}
	data, digest, err := privateFileBytes(path)
	if err != nil {
		return nil, err
	}
	return map[string]any{"path": path, "content_b64": base64.StdEncoding.EncodeToString(data), "digest": digest}, nil
}

func secretFileWrite(params map[string]any, adm *Admission) (any, error) {
	if adm == nil {
		return nil, fmt.Errorf("execution admission is required")
	}
	if err := secretDeadline(params); err != nil {
		return nil, err
	}
	data, err := base64.StdEncoding.DecodeString(paramString(params, "content_b64"))
	if err != nil || len(data) > maxSecretFile {
		return nil, fmt.Errorf("invalid private file payload")
	}
	secretFileLock.Lock()
	defer secretFileLock.Unlock()
	path, err := secretPath(params, adm, true)
	if err != nil {
		return nil, err
	}
	_, digest, err := privateFileBytes(path)
	if err != nil {
		return nil, err
	}
	if digest != paramString(params, "digest") {
		return nil, fmt.Errorf("private file changed; apply again")
	}
	f, err := os.CreateTemp(filepath.Dir(path), ".tomo-secret-*")
	if err != nil {
		return nil, fmt.Errorf("could not create private file")
	}
	name := f.Name()
	defer os.Remove(name)
	if _, err = f.Write(data); err == nil {
		err = f.Sync()
	}
	closeErr := f.Close()
	if err != nil || closeErr != nil {
		return nil, fmt.Errorf("could not write private file")
	}
	if err = os.Rename(name, path); err != nil {
		return nil, fmt.Errorf("could not replace private file")
	}
	return map[string]any{"path": path, "ok": true}, nil
}

func secretHTTP(params map[string]any, adm *Admission) (any, error) {
	if adm == nil {
		return nil, fmt.Errorf("execution admission is required")
	}
	if err := secretDeadline(params); err != nil {
		return nil, err
	}
	body, err := base64.StdEncoding.DecodeString(paramString(params, "body_b64"))
	if err != nil || len(body) > 4_000_000 {
		return nil, fmt.Errorf("invalid private HTTP request")
	}
	req, err := http.NewRequest(paramString(params, "method"), paramString(params, "url"), bytes.NewReader(body))
	if err != nil || (req.URL.Scheme != "http" && req.URL.Scheme != "https") || req.URL.User != nil {
		return nil, fmt.Errorf("invalid private HTTP request")
	}
	headers, ok := params["headers"].(map[string]any)
	if !ok {
		return nil, fmt.Errorf("invalid private HTTP headers")
	}
	for key, value := range headers {
		if strings.EqualFold(key, "accept-encoding") {
			continue
		} // Let Go negotiate/decompress gzip.
		text, ok := value.(string)
		if !ok {
			return nil, fmt.Errorf("invalid private HTTP headers")
		}
		req.Header.Set(key, text)
	}
	seconds, ok := asFloat(params["timeout"])
	if !ok || seconds <= 0 || seconds > 60 {
		return nil, fmt.Errorf("invalid private HTTP timeout")
	}
	transport := http.DefaultTransport.(*http.Transport).Clone()
	transport.Proxy = nil
	defer transport.CloseIdleConnections()
	client := &http.Client{Transport: transport, Timeout: time.Duration(seconds * float64(time.Second)), CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}
	response, err := client.Do(req)
	if err != nil {
		return nil, fmt.Errorf("private HTTP request failed")
	}
	defer response.Body.Close()
	data, err := io.ReadAll(io.LimitReader(response.Body, 2_000_001))
	if err != nil || len(data) > 2_000_000 {
		return nil, fmt.Errorf("private HTTP response exceeds limits or is unavailable")
	}
	return map[string]any{"status_code": response.StatusCode, "body_b64": base64.StdEncoding.EncodeToString(data)}, nil
}
