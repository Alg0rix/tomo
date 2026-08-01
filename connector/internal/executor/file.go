package executor

import (
	"bytes"
	"encoding/base64"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"regexp"
	"strings"
)

func readFile(params map[string]any) (any, error) {
	pathArg := paramString(params, "path")
	root := WorkRoot()
	target, err := resolvePath(pathArg, root)
	if err != nil {
		return nil, fmt.Errorf("read_file error: %w", err)
	}
	if st, err := os.Stat(target); err == nil && st.IsDir() {
		return nil, fmt.Errorf("read_file error: path is a directory, not a file: %s", target)
	}
	data, err := os.ReadFile(target)
	if err != nil {
		return nil, fmt.Errorf("read_file error: %w", err)
	}
	return map[string]any{
		"content": string(data),
		"size":    len(data),
		"path":    target,
	}, nil
}

func writeFile(params map[string]any) (any, error) {
	pathArg := paramString(params, "path")
	content, ok := params["content"].(string)
	if !ok {
		if params["content"] == nil {
			content = ""
		} else {
			return nil, fmt.Errorf("'content' argument must be a string")
		}
	}
	mode := strings.ToLower(strings.TrimSpace(paramString(params, "mode")))
	if mode == "" {
		mode = "overwrite"
	}
	root := WorkRoot()
	target, err := resolvePath(pathArg, root)
	if err != nil {
		return nil, fmt.Errorf("write_file error: %w", err)
	}
	if mode == "create" {
		if _, err := os.Stat(target); err == nil {
			return nil, fmt.Errorf("file already exists: %s. Use mode=overwrite to replace, or str_replace/patch for edits", pathArg)
		} else if !os.IsNotExist(err) {
			return nil, fmt.Errorf("stat error: %w", err)
		}
	}
	if err := os.MkdirAll(filepath.Dir(target), 0o755); err != nil {
		return nil, fmt.Errorf("mkdir error: %w", err)
	}
	flag := os.O_WRONLY | os.O_CREATE | os.O_TRUNC
	if mode == "append" {
		flag = os.O_WRONLY | os.O_CREATE | os.O_APPEND
	}
	f, err := os.OpenFile(target, flag, 0o644)
	if err != nil {
		return nil, fmt.Errorf("open error: %w", err)
	}
	defer f.Close()
	if _, err := f.WriteString(content); err != nil {
		return nil, fmt.Errorf("write error: %w", err)
	}
	return map[string]any{"ok": true, "path": target, "mode": mode}, nil
}

func strReplace(params map[string]any) (any, error) {
	pathArg := paramString(params, "path")
	old := paramString(params, "old_string")
	newS, ok := params["new_string"].(string)
	if !ok {
		return nil, fmt.Errorf("'new_string' argument must be a string")
	}
	if old == "" {
		return nil, fmt.Errorf("'old_string' argument must be a non-empty string")
	}
	wantCount := 1
	if f, ok := asFloat(params["count"]); ok {
		wantCount = int(f)
	}
	if wantCount < 1 && wantCount != -1 {
		return nil, fmt.Errorf("'count' must be >= 1, or -1 to replace all")
	}
	root := WorkRoot()
	target, err := resolvePath(pathArg, root)
	if err != nil {
		return nil, err
	}
	data, err := os.ReadFile(target)
	if err != nil {
		if os.IsNotExist(err) {
			return nil, fmt.Errorf("file not found: %s", pathArg)
		}
		return nil, fmt.Errorf("could not read file: %w", err)
	}
	text := string(data)
	effOld, effNew, occ := matchReplace(text, old, newS)
	if occ == 0 {
		return nil, fmt.Errorf("old_string not found in file. Action: call read_file and copy the exact text to replace")
	}
	limit := wantCount
	if wantCount == -1 {
		limit = occ
	} else if occ != wantCount {
		return nil, fmt.Errorf("old_string found %d time(s), but count=%d. Add more context or set count=%d (or -1 for all)", occ, wantCount, occ)
	}
	updated := strings.Replace(text, effOld, effNew, limit)
	if err := os.WriteFile(target, []byte(updated), 0o644); err != nil {
		return nil, fmt.Errorf("could not write file: %w", err)
	}
	return map[string]any{"ok": true, "path": target, "replacements": limit}, nil
}

// matchReplace: exact, then unescape, then smart-quote normalize.
func matchReplace(content, old, newS string) (effOld, effNew string, occ int) {
	if n := strings.Count(content, old); n > 0 {
		return old, newS, n
	}
	uOld := unescapeLLM(old)
	if uOld != old {
		if n := strings.Count(content, uOld); n > 0 {
			return uOld, unescapeLLM(newS), n
		}
	}
	nOld := normalizeQuotes(old)
	nContent := normalizeQuotes(content)
	if idx := strings.Index(nContent, nOld); idx >= 0 {
		// Best-effort: use equal-length slice from original when lengths align.
		end := idx + len(old)
		if end > len(content) {
			end = idx + len(nOld)
		}
		if end > len(content) {
			end = len(content)
		}
		slice := content[idx:end]
		if normalizeQuotes(slice) == nOld {
			if n := strings.Count(content, slice); n > 0 {
				return slice, normalizeQuotes(newS), n
			}
		}
		if n := strings.Count(content, nOld); n > 0 {
			return nOld, normalizeQuotes(newS), n
		}
	}
	return old, newS, 0
}

func unescapeLLM(s string) string {
	s = strings.ReplaceAll(s, `\"`, `"`)
	s = strings.ReplaceAll(s, `\'`, `'`)
	return s
}

func normalizeQuotes(s string) string {
	replacer := strings.NewReplacer(
		"\u2018", "'", "\u2019", "'", "\u201a", "'", "\u201b", "'",
		"\u201c", `"`, "\u201d", `"`, "\u201e", `"`,
		"\u2032", "'", "\u2033", `"`,
	)
	return replacer.Replace(s)
}

func applyPatch(params map[string]any) (any, error) {
	pathArg := paramString(params, "path")
	patchText, ok := params["patch"].(string)
	if !ok || strings.TrimSpace(patchText) == "" {
		return nil, fmt.Errorf("'patch' argument must be a non-empty string")
	}
	root := WorkRoot()
	target, err := resolvePath(pathArg, root)
	if err != nil {
		return nil, err
	}
	creating := isCreateNewFilePatch(patchText)
	var raw string
	if _, err := os.Stat(target); err != nil {
		if !os.IsNotExist(err) {
			return nil, fmt.Errorf("could not stat file: %w", err)
		}
		if !creating {
			return nil, fmt.Errorf("file not found: %s", pathArg)
		}
		if err := os.MkdirAll(filepath.Dir(target), 0o755); err != nil {
			return nil, fmt.Errorf("mkdir error: %w", err)
		}
		raw = ""
	} else {
		data, err := os.ReadFile(target)
		if err != nil {
			return nil, fmt.Errorf("could not read file: %w", err)
		}
		raw = string(data)
	}
	out, n, err := applyPatchToContent(raw, patchText)
	if err != nil {
		return nil, err
	}
	if err := os.WriteFile(target, []byte(out), 0o644); err != nil {
		return nil, fmt.Errorf("could not write file: %w", err)
	}
	return map[string]any{"ok": true, "path": target, "hunks_applied": n}, nil
}

func deleteFile(params map[string]any) (any, error) {
	pathArg := paramString(params, "path")
	root := WorkRoot()
	target, err := resolvePath(pathArg, root)
	if err != nil {
		return nil, err
	}
	st, err := os.Stat(target)
	if err != nil {
		if os.IsNotExist(err) {
			return nil, fmt.Errorf("file not found: %s", pathArg)
		}
		return nil, err
	}
	if st.IsDir() {
		return nil, fmt.Errorf("path is a directory; delete_file only removes files")
	}
	if err := os.Remove(target); err != nil {
		return nil, fmt.Errorf("could not delete file: %w", err)
	}
	return map[string]any{"ok": true, "path": target}, nil
}

func searchFiles(params map[string]any) (any, error) {
	pattern := paramString(params, "pattern")
	if pattern == "" {
		return nil, fmt.Errorf("'pattern' argument must be a non-empty string")
	}
	globPat := paramString(params, "glob")
	// Content patterns are regex by default.
	useRegex := true
	if b, ok := params["regex"].(bool); ok {
		useRegex = b
	}
	var re *regexp.Regexp
	if useRegex {
		var err error
		re, err = regexp.Compile(pattern)
		if err != nil {
			return nil, fmt.Errorf("invalid regex: %w", err)
		}
	}
	root := strings.TrimRight(WorkRoot(), string(os.PathSeparator))
	const maxMatches = 50
	const maxSnippet = 200
	skip := map[string]bool{".git": true, "__pycache__": true, "node_modules": true, ".venv": true, "venv": true}
	matches := []string{}
	err := filepath.WalkDir(root, func(path string, d os.DirEntry, err error) error {
		if err != nil {
			return nil
		}
		if d.IsDir() {
			if skip[d.Name()] {
				return filepath.SkipDir
			}
			return nil
		}
		if globPat != "" {
			ok, _ := filepath.Match(globPat, d.Name())
			if !ok {
				return nil
			}
		}
		data, rerr := os.ReadFile(path)
		if rerr != nil || bytes.IndexByte(data, 0) >= 0 {
			return nil
		}
		rel, _ := filepath.Rel(root, path)
		rel = filepath.ToSlash(rel)
		for i, line := range strings.Split(string(data), "\n") {
			hit := false
			if re != nil {
				hit = re.MatchString(line)
			} else {
				hit = strings.Contains(line, pattern)
			}
			if !hit {
				continue
			}
			snippet := strings.TrimSpace(line)
			if len(snippet) > maxSnippet {
				snippet = snippet[:maxSnippet] + "…"
			}
			matches = append(matches, fmt.Sprintf("%s:%d:%s", rel, i+1, snippet))
			if len(matches) >= maxMatches {
				return io.EOF
			}
		}
		return nil
	})
	if err != nil && err != io.EOF {
		return nil, err
	}
	return map[string]any{
		"matches": matches,
		"count":   len(matches),
		"capped":  len(matches) >= maxMatches,
	}, nil
}

func readFileB64(params map[string]any) (any, error) {
	pathArg := paramString(params, "path")
	root := WorkRoot()
	path := resolvePathAbs(pathArg, root)
	fi, err := os.Stat(path)
	if err != nil {
		return nil, fmt.Errorf("read_file_b64 error: %w", err)
	}
	if fi.IsDir() {
		return nil, fmt.Errorf("read_file_b64 error: path is a directory")
	}
	totalSize := fi.Size()
	offset := int64(0)
	if f, ok := asFloat(params["offset"]); ok && f > 0 {
		offset = int64(f)
	}
	size := 0
	if f, ok := asFloat(params["size"]); ok && f > 0 {
		size = int(f)
	}
	f, err := os.Open(path)
	if err != nil {
		return nil, fmt.Errorf("read_file_b64 error: %w", err)
	}
	defer f.Close()
	if offset > 0 {
		if _, err := f.Seek(offset, io.SeekStart); err != nil {
			return nil, fmt.Errorf("seek error: %w", err)
		}
	}
	readSize := totalSize - offset
	if size > 0 && int64(size) < readSize {
		readSize = int64(size)
	}
	if readSize < 0 {
		readSize = 0
	}
	buf := make([]byte, readSize)
	n, err := io.ReadFull(f, buf)
	if err != nil && err != io.EOF && err != io.ErrUnexpectedEOF {
		return nil, fmt.Errorf("read error: %w", err)
	}
	buf = buf[:n]
	return map[string]any{
		"data":       base64.StdEncoding.EncodeToString(buf),
		"bytes_read": n,
		"total_size": totalSize,
		"path":       path,
	}, nil
}

func writeFileB64(params map[string]any) (any, error) {
	pathArg := paramString(params, "path")
	dataB64 := paramString(params, "data")
	root := WorkRoot()
	path := resolvePathAbs(pathArg, root)
	decoded, err := base64.StdEncoding.DecodeString(dataB64)
	if err != nil {
		return nil, fmt.Errorf("base64 decode error: %w", err)
	}
	offset := int64(0)
	if f, ok := asFloat(params["offset"]); ok && f > 0 {
		offset = int64(f)
	}
	isLast := true
	if b, ok := params["is_last"].(bool); ok {
		isLast = b
	}
	partPath := path + ".part"
	if offset == 0 {
		if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
			return nil, fmt.Errorf("mkdir error: %w", err)
		}
		if err := os.WriteFile(partPath, decoded, 0o644); err != nil {
			return nil, fmt.Errorf("create error: %w", err)
		}
	} else {
		f, err := os.OpenFile(partPath, os.O_WRONLY|os.O_APPEND, 0o644)
		if err != nil {
			return nil, fmt.Errorf("open error: %w", err)
		}
		if _, err := f.Write(decoded); err != nil {
			_ = f.Close()
			return nil, fmt.Errorf("write error: %w", err)
		}
		_ = f.Close()
	}
	if isLast {
		if err := os.Rename(partPath, path); err != nil {
			return nil, fmt.Errorf("rename error: %w", err)
		}
	}
	return map[string]any{"ok": true, "path": path}, nil
}
