"""Pure text_edit unit tests (no sandbox I/O)."""

from __future__ import annotations

from app.runtime.tools.text_edit import (
    apply_patch_to_content,
    apply_str_replace,
    parse_hunks,
)


def test_str_replace_exact() -> None:
    out = apply_str_replace("hello world\n", "world", "tomo")
    assert out == ("hello tomo\n", 1)


def test_str_replace_count_all() -> None:
    out = apply_str_replace("aa aa aa", "aa", "bb", count=-1)
    assert out == ("bb bb bb", 3)


def test_str_replace_count_mismatch() -> None:
    err = apply_str_replace("aa aa", "aa", "bb", count=1)
    assert isinstance(err, str) and "2 time" in err


def test_str_replace_unescape_quotes() -> None:
    content = 'x = "hi"\n'
    # LLM double-escaped old_string
    out = apply_str_replace(content, 'x = \\"hi\\"\n', 'x = "yo"\n')
    assert out == ('x = "yo"\n', 1)


def test_str_replace_smart_quotes() -> None:
    content = "say \u201chello\u201d\n"
    out = apply_str_replace(content, 'say "hello"\n', 'say "hi"\n')
    assert not isinstance(out, str)
    assert "hi" in out[0]


def test_parse_hunks_skips_git_headers() -> None:
    patch = (
        "diff --git a/f b/f\n"
        "--- a/f\n"
        "+++ b/f\n"
        "@@ -1,2 +1,2 @@\n"
        " foo\n"
        "-bar\n"
        "+BAR\n"
    )
    hunks = parse_hunks(patch)
    assert len(hunks) == 1
    assert hunks[0].old_start == 1


def test_patch_replace_line() -> None:
    raw = "line one\nline two\nline three\n"
    patch = "@@ -1,3 +1,3 @@\n line one\n-line two\n+line TWO\n line three\n"
    r = apply_patch_to_content(raw, patch)
    assert r["result"] == "success"
    assert r["content"] == "line one\nline TWO\nline three\n"


def test_patch_insertion_only() -> None:
    raw = "line1\nline2\nline3\n"
    patch = "@@ -2,0 +2,2 @@\n+new_a\n+new_b\n"
    r = apply_patch_to_content(raw, patch)
    assert r["result"] == "success"
    assert r["content"] == "line1\nnew_a\nnew_b\nline2\nline3\n"


def test_patch_create_new() -> None:
    patch = "@@ -0,0 +1,2 @@\n+first\n+second\n"
    r = apply_patch_to_content("", patch)
    assert r["result"] == "success"
    assert r["content"] == "first\nsecond\n"


def test_patch_multi_hunk() -> None:
    raw = "a\nb\nc\nd\ne\nf\n"
    patch = "@@ -1,2 +1,2 @@\n a\n-b\n+B\n@@ -5,2 +5,2 @@\n e\n-f\n+F\n"
    r = apply_patch_to_content(raw, patch)
    assert r["result"] == "success"
    assert r["content"] == "a\nB\nc\nd\ne\nF\n"


def test_patch_drift_tolerance() -> None:
    lines = [f"filler_{i}\n" for i in range(40)]
    lines += ["target line\n", "after target\n"]
    raw = "".join(lines)
    # Wrong line number; still finds by context
    patch = "@@ -1,2 +1,2 @@\n target line\n-after target\n+REPLACED\n"
    r = apply_patch_to_content(raw, patch)
    assert r["result"] == "success"
    assert "REPLACED" in str(r["content"])


def test_patch_crlf_preserved() -> None:
    raw = "line1\r\nline2\r\nline3\r\n"
    patch = "@@ -2,1 +2,1 @@\n-line2\n+LINE2\n"
    r = apply_patch_to_content(raw, patch)
    assert r["result"] == "success"
    assert "\r\n" in str(r["content"])
    assert "LINE2" in str(r["content"])


def test_patch_context_missing() -> None:
    r = apply_patch_to_content(
        "a\nb\n", "@@ -1,2 +1,2 @@\n WRONG\n-b\n+B\n"
    )
    assert "error" in r
    assert "read_file" in str(r["error"]).lower() or "Action" in str(r["error"])
