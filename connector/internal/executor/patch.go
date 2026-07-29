package executor

import (
	"fmt"
	"regexp"
	"strconv"
	"strings"
)

const searchWindow = 80

type hunkLine struct {
	op   byte // ' ', '-', '+'
	text string
	noNL bool
}

type hunk struct {
	oldStart int
	oldCount int
	newStart int
	newCount int
	lines    []hunkLine
}

var hunkHeaderRE = regexp.MustCompile(`^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@`)
var gitHeaderRE = regexp.MustCompile(`^(diff --git|index |old mode|new mode|deleted file|new file)`)

func parseHunks(patchText string) []hunk {
	var hunks []hunk
	var cur *hunk
	for _, raw := range strings.Split(patchText, "\n") {
		line := strings.TrimRight(raw, "\r")
		if gitHeaderRE.MatchString(line) {
			continue
		}
		if strings.HasPrefix(line, "--- ") || strings.HasPrefix(line, "+++ ") {
			if cur != nil {
				hunks = append(hunks, *cur)
				cur = nil
			}
			continue
		}
		if m := hunkHeaderRE.FindStringSubmatch(line); m != nil {
			if cur != nil {
				hunks = append(hunks, *cur)
			}
			oldStart, _ := strconv.Atoi(m[1])
			oldCount := 1
			if m[2] != "" {
				oldCount, _ = strconv.Atoi(m[2])
			}
			newStart, _ := strconv.Atoi(m[3])
			newCount := 1
			if m[4] != "" {
				newCount, _ = strconv.Atoi(m[4])
			}
			h := hunk{oldStart: oldStart, oldCount: oldCount, newStart: newStart, newCount: newCount}
			cur = &h
			continue
		}
		if cur == nil {
			continue
		}
		if strings.HasPrefix(line, `\ `) {
			if n := len(cur.lines); n > 0 {
				cur.lines[n-1].noNL = true
			}
			continue
		}
		if strings.HasPrefix(line, "-") {
			cur.lines = append(cur.lines, hunkLine{op: '-', text: line[1:]})
		} else if strings.HasPrefix(line, "+") {
			cur.lines = append(cur.lines, hunkLine{op: '+', text: line[1:]})
		} else if strings.HasPrefix(line, " ") {
			cur.lines = append(cur.lines, hunkLine{op: ' ', text: line[1:]})
		} else {
			cur.lines = append(cur.lines, hunkLine{op: ' ', text: line})
		}
	}
	if cur != nil {
		hunks = append(hunks, *cur)
	}
	return hunks
}

func isCreateNewFilePatch(patchText string) bool {
	hunks := parseHunks(patchText)
	if len(hunks) == 0 {
		return false
	}
	for _, h := range hunks {
		if !(h.oldStart == 0 && h.oldCount == 0) {
			return false
		}
	}
	return true
}

func findHunkPos(lines []string, hl []hunkLine, stated int) int {
	var toMatch []string
	for _, l := range hl {
		if l.op == ' ' || l.op == '-' {
			toMatch = append(toMatch, l.text)
		}
	}
	if len(toMatch) == 0 {
		pos := stated
		if pos < 0 {
			pos = 0
		}
		if pos > len(lines) {
			pos = len(lines)
		}
		return pos
	}
	matchLen := len(toMatch)

	try := func(transform func(string) string) int {
		want := make([]string, matchLen)
		got := make([]string, len(lines))
		for i, t := range toMatch {
			want[i] = transform(t)
		}
		for i, l := range lines {
			got[i] = transform(strings.TrimRight(l, "\r"))
		}
		for delta := 0; delta <= searchWindow; delta++ {
			signs := []int{0}
			if delta > 0 {
				signs = []int{1, -1}
			}
			for _, sign := range signs {
				pos := stated + sign*delta
				if pos < 0 || pos+matchLen > len(got) {
					continue
				}
				ok := true
				for i := 0; i < matchLen; i++ {
					if got[pos+i] != want[i] {
						ok = false
						break
					}
				}
				if ok {
					return pos
				}
			}
		}
		for pos := 0; pos+matchLen <= len(got); pos++ {
			ok := true
			for i := 0; i < matchLen; i++ {
				if got[pos+i] != want[i] {
					ok = false
					break
				}
			}
			if ok {
				return pos
			}
		}
		return -1
	}

	if p := try(func(s string) string { return strings.TrimRight(s, " \t") }); p >= 0 {
		return p
	}
	if p := try(strings.TrimSpace); p >= 0 {
		return p
	}
	if p := try(func(s string) string { return strings.TrimRight(unescapeLLM(s), " \t") }); p >= 0 {
		return p
	}
	if p := try(func(s string) string { return strings.TrimRight(normalizeQuotes(s), " \t") }); p >= 0 {
		return p
	}
	if p := try(func(s string) string {
		return strings.TrimRight(strings.ReplaceAll(s, "\t", "    "), " \t")
	}); p >= 0 {
		return p
	}
	return -1
}

func applyPatchToContent(raw, patchText string) (string, int, error) {
	hunks := parseHunks(patchText)
	if len(hunks) == 0 {
		return "", 0, fmt.Errorf("no valid hunks found in patch. Need @@ headers. For simple edits prefer str_replace")
	}
	crlf := strings.Contains(raw, "\r\n")
	content := strings.ReplaceAll(raw, "\r\n", "\n")
	var lines []string
	trailing := false
	if strings.HasSuffix(content, "\n") {
		trailing = true
		body := strings.TrimSuffix(content, "\n")
		if body == "" && content == "\n" {
			lines = []string{""}
		} else if body == "" {
			lines = []string{}
		} else {
			lines = strings.Split(body, "\n")
		}
	} else if content != "" {
		lines = strings.Split(content, "\n")
	}

	offset := 0
	for _, h := range hunks {
		if h.oldCount == 0 {
			insertPos := h.newStart - 1 + offset
			if insertPos < 0 {
				insertPos = 0
			}
			if insertPos > len(lines) {
				insertPos = len(lines)
			}
			var newLines []string
			for _, l := range h.lines {
				if l.op == '+' {
					newLines = append(newLines, l.text)
				}
			}
			out := make([]string, 0, len(lines)+len(newLines))
			out = append(out, lines[:insertPos]...)
			out = append(out, newLines...)
			out = append(out, lines[insertPos:]...)
			lines = out
			offset += len(newLines)
			if len(newLines) > 0 {
				trailing = true
			}
			continue
		}
		stated := h.oldStart - 1 + offset
		pos := findHunkPos(lines, h.lines, stated)
		if pos < 0 {
			return "", 0, fmt.Errorf("context not found for hunk at line %d. Action: read_file and rebuild the patch from current content", h.oldStart)
		}
		var result []string
		fileIdx := pos
		for _, l := range h.lines {
			switch l.op {
			case ' ':
				result = append(result, lines[fileIdx])
				fileIdx++
			case '-':
				fileIdx++
			case '+':
				result = append(result, l.text)
			}
		}
		consumed, produced := 0, 0
		for _, l := range h.lines {
			if l.op == ' ' || l.op == '-' {
				consumed++
			}
			if l.op == ' ' || l.op == '+' {
				produced++
			}
		}
		out := make([]string, 0, len(lines)-consumed+produced)
		out = append(out, lines[:pos]...)
		out = append(out, result...)
		out = append(out, lines[pos+consumed:]...)
		lines = out
		offset += produced - consumed
	}

	res := strings.Join(lines, "\n")
	if trailing && len(lines) > 0 {
		res += "\n"
	}
	if crlf {
		res = strings.ReplaceAll(res, "\n", "\r\n")
	}
	return res, len(hunks), nil
}
