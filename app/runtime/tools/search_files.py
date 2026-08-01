"""search_files — prefer ripgrep, then grep/find, then Python.

Cascade (local workplace / sandbox):
  content:  ``rg`` → ``grep -rn`` → pure Python walk
  files:    ``rg --files`` → ``find`` → pure Python walk

Content patterns are regex by default. Pass ``regex=false``
for fixed-string (``rg -F`` / ``grep -F``). Always has a Python last resort
when rg/grep/find are missing.
"""

from __future__ import annotations

import fnmatch
import os
import re
import shutil
import signal
import subprocess
from pathlib import Path
from typing import Any

from app.runtime.tools.file_util import parse_positive_int
from app.runtime.tools.sandbox import jail_path, resolve_work_root
from app.runtime.tools.tunnel_rpc import try_tunnel_rpc

_MAX_MATCHES = 50
_MAX_SNIPPET = 200
_SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv", ".tomo"}
# Keep short: workplace root can be `/` (sandbox-root) — never hang the agent.
_TIMEOUT = 20


def run(arguments: dict[str, Any]) -> str:
    """Search content or find files by name; always returns a string."""
    if not isinstance(arguments, dict):
        return "Error: search_files expects a dict of arguments"
    pattern = arguments.get("pattern")
    if not isinstance(pattern, str) or not pattern:
        return "Error: 'pattern' argument must be a non-empty string"

    target_mode = str(arguments.get("target") or "content").strip().lower()
    if target_mode in ("grep",):
        target_mode = "content"
    if target_mode in ("find", "files"):
        target_mode = "files"
    if target_mode not in ("content", "files"):
        return "Error: 'target' must be 'content' or 'files'"

    glob_pat = arguments.get("glob") or arguments.get("file_glob")
    if glob_pat is not None and not isinstance(glob_pat, str):
        return "Error: 'glob' argument must be a string"

    path_arg = arguments.get("path") or "."
    if not isinstance(path_arg, str) or not path_arg.strip():
        path_arg = "."

    # Content patterns are regex; filename search is glob.
    # Opt out of regex content with regex=false (-F). Opt into regex
    # filenames with regex=true.
    if "regex" in arguments:
        use_regex = bool(arguments.get("regex"))
    else:
        use_regex = target_mode == "content"

    output_mode = str(arguments.get("output_mode") or "content").strip().lower()
    if output_mode not in ("content", "files_only", "count"):
        return "Error: 'output_mode' must be content, files_only, or count"

    context = parse_positive_int(
        arguments.get("context", 0), 0, name="context", minimum=0
    )
    if isinstance(context, str):
        return context
    context = min(int(context), 5)

    limit = parse_positive_int(
        arguments.get("limit", _MAX_MATCHES),
        _MAX_MATCHES,
        name="limit",
        minimum=1,
    )
    if isinstance(limit, str):
        return limit
    limit = min(int(limit), 200)

    offset = parse_positive_int(
        arguments.get("offset", 0), 0, name="offset", minimum=0
    )
    if isinstance(offset, str):
        return offset

    # Tunnel: still use connector search when simple content search at root.
    if target_mode == "content" and path_arg in (".", ""):
        remote = try_tunnel_rpc(
            "search_files",
            {
                "pattern": pattern,
                "glob": glob_pat or "",
                "regex": use_regex,
            },
        )
        if remote is not None:
            return remote

    root = resolve_work_root()
    base = jail_path(root, path_arg)
    if isinstance(base, str):
        return base
    if not base.exists():
        return f"Error: path not found: {path_arg}"
    search_root = base if base.is_dir() else base.parent

    # Never walk the whole machine. Ops often has local workplace root=/
    # (sandbox-root) — that made every search run as `rg /` and hang/timeout.
    if _is_filesystem_root(search_root):
        return (
            "Error: workplace root is the filesystem root (/). "
            "Refusing to scan the entire disk with ripgrep. "
            "Pick a project folder for this chat (Browse… → e.g. "
            "/home/dev-serv/Project/py-proj/tomo) or pass path= to a "
            "subdirectory (e.g. path=home/dev-serv/…)."
        )

    if target_mode == "files":
        return _search_files_by_name(
            root=root,
            search_root=search_root,
            pattern=pattern,
            use_regex=use_regex,
            limit=limit,
            offset=int(offset),
        )

    return _search_content(
        root=root,
        search_root=search_root,
        pattern=pattern,
        glob_pat=glob_pat,
        use_regex=use_regex,
        output_mode=output_mode,
        context=context,
        limit=limit,
        offset=int(offset),
    )


# ── command helpers ──────────────────────────────────────────────────────


def _is_filesystem_root(path: Path) -> bool:
    try:
        p = path.resolve()
        return p == p.anchor or str(p) in ("/", "\\")
    except OSError:
        return str(path) in ("/", "\\")


def _has_cmd(name: str) -> bool:
    return shutil.which(name) is not None


def _run(argv: list[str], *, cwd: Path, timeout: int = _TIMEOUT) -> tuple[int, str]:
    """Run argv; kill the whole process group on timeout (rg spawns workers)."""
    try:
        proc = subprocess.Popen(
            argv,
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
            env={**os.environ, "TERM": "dumb"},
        )
    except OSError as exc:
        return 127, f"Error: could not run {argv[0]}: {exc}"
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            try:
                proc.kill()
            except OSError:
                pass
        try:
            proc.wait(timeout=3)
        except Exception:
            pass
        return 124, "Error: search timed out (try a narrower path than the workplace root)"
    out = out or ""
    err = (err or "").strip()
    code = int(proc.returncode or 0)
    if code not in (0, 1) and err and not out.strip():
        return code, err
    # Prefer stdout; attach short stderr for parse errors when stdout empty.
    if not out.strip() and err:
        return code, err
    return code, out


def _rel(root: Path, path_str: str) -> str:
    try:
        p = Path(path_str)
        if not p.is_absolute():
            p = (root / path_str).resolve()
        else:
            p = p.resolve()
        return p.relative_to(root.resolve()).as_posix()
    except (ValueError, OSError):
        return path_str.replace("\\", "/")


# ── content search ───────────────────────────────────────────────────────


def _search_content(
    *,
    root: Path,
    search_root: Path,
    pattern: str,
    glob_pat: str | None,
    use_regex: bool,
    output_mode: str,
    context: int,
    limit: int,
    offset: int,
) -> str:
    if _has_cmd("rg"):
        out = _content_rg(
            root,
            search_root,
            pattern,
            glob_pat,
            use_regex,
            output_mode,
            context,
            limit,
            offset,
        )
        if out is not None:
            return out
    if _has_cmd("grep"):
        out = _content_grep(
            root,
            search_root,
            pattern,
            glob_pat,
            use_regex,
            output_mode,
            context,
            limit,
            offset,
        )
        if out is not None:
            return out
    return _content_python(
        root,
        search_root,
        pattern,
        glob_pat,
        use_regex,
        output_mode,
        context,
        limit,
        offset,
    )


def _content_rg(
    root: Path,
    search_root: Path,
    pattern: str,
    glob_pat: str | None,
    use_regex: bool,
    output_mode: str,
    context: int,
    limit: int,
    offset: int,
) -> str | None:
    """Return result string, or None to fall through."""
    cmd: list[str] = [
        "rg",
        "--line-number",
        "--no-heading",
        "--with-filename",
        "--color",
        "never",
        "--line-buffered",
        "--max-filesize",
        "2M",
    ]
    if not use_regex:
        cmd.append("-F")
    if context > 0 and output_mode == "content":
        cmd.extend(["-C", str(context)])
    else:
        # Cap matches per file so wide trees (e.g. workplace root=/) stay fast.
        cmd.extend(["-m", str(max(1, min(20, limit + offset)))])
    if glob_pat:
        cmd.extend(["--glob", glob_pat])
    # Skip common junk (in addition to rg defaults / gitignore)
    for g in ("!**/.git/**", "!**/node_modules/**", "!**/.venv/**", "!**/__pycache__/**"):
        cmd.extend(["--glob", g])
    if output_mode == "files_only":
        cmd.append("-l")
    elif output_mode == "count":
        cmd.append("-c")
    cmd.extend(["--", pattern, str(search_root)])

    code, stdout = _run(cmd, cwd=root)
    if code == 124:
        return "Error: search timed out"
    if stdout.startswith("Error:"):
        return stdout
    # rg exit: 0=hits, 1=no hits, 2=error (regex parse, etc.)
    if code == 2:
        msg = (stdout or "").strip()
        low = msg.casefold()
        if "regex" in low or "parse error" in low or "syntax error" in low:
            return f"Error: invalid regex: {msg[:300]}"
        if not msg:
            return None  # try grep/python fallback
        # partial tree errors with no matches — fall through
        if not any(c.isdigit() and ":" in line for line in msg.splitlines() for c in line[:1]):
            return None
    if code not in (0, 1, 2):
        return None
    if code == 2:
        # Has some payload; try format, else fallback
        formatted = _format_rg_output(
            root, stdout, output_mode=output_mode, context=context, limit=limit, offset=offset
        )
        if formatted.startswith("No matches"):
            return None
        return formatted

    return _format_rg_output(
        root, stdout, output_mode=output_mode, context=context, limit=limit, offset=offset
    )


def _format_rg_output(
    root: Path,
    stdout: str,
    *,
    output_mode: str,
    context: int,
    limit: int,
    offset: int,
) -> str:
    lines = [ln for ln in stdout.splitlines() if ln and ln != "--"]

    if output_mode == "files_only":
        files: list[str] = []
        for ln in lines:
            files.append(_rel(root, ln.strip()))
        page = files[offset : offset + limit]
        if not page:
            return "No matches"
        header = f"{len(page)} file(s)"
        if len(files) > offset + limit:
            header += f" (capped at {limit})"
        return header + "\n" + "\n".join(page)

    if output_mode == "count":
        counts: dict[str, int] = {}
        for ln in lines:
            if ":" not in ln:
                continue
            path_part, _, num = ln.rpartition(":")
            try:
                n = int(num)
            except ValueError:
                continue
            counts[_rel(root, path_part)] = n
        if not counts:
            return "No matches"
        total = sum(counts.values())
        body = "\n".join(f"{p}: {n}" for p, n in sorted(counts.items())[:limit])
        return f"{total} match(es) in {len(counts)} file(s)\n{body}"

    # content: path:line:text  or with -C: path-line-text for context
    match_re = re.compile(r"^(.*?):(\d+):(.*)$")
    ctx_re = re.compile(r"^(.*?)-(\d+)-(.*)$")
    entries: list[str] = []
    for ln in lines:
        m = match_re.match(ln)
        if m:
            rel = _rel(root, m.group(1))
            snippet = m.group(3)
            if len(snippet) > _MAX_SNIPPET:
                snippet = snippet[:_MAX_SNIPPET] + "…"
            mark = ">" if context > 0 else ""
            entries.append(f"{rel}:{m.group(2)}:{mark}{snippet}")
            continue
        if context > 0:
            c = ctx_re.match(ln)
            if c:
                rel = _rel(root, c.group(1))
                snippet = c.group(3)
                if len(snippet) > _MAX_SNIPPET:
                    snippet = snippet[:_MAX_SNIPPET] + "…"
                entries.append(f"{rel}:{c.group(2)}: {snippet}")
    page = entries[offset : offset + limit]
    if not page:
        return "No matches"
    header = f"{len(page)} match(es)"
    if len(entries) > offset + limit:
        header += f" (capped at {limit})"
    if offset:
        header += f" offset={offset}"
    return header + "\n" + "\n".join(page)


def _content_grep(
    root: Path,
    search_root: Path,
    pattern: str,
    glob_pat: str | None,
    use_regex: bool,
    output_mode: str,
    context: int,
    limit: int,
    offset: int,
) -> str | None:
    cmd = ["grep", "-rnH", "--exclude-dir=.git", "--exclude-dir=node_modules",
           "--exclude-dir=.venv", "--exclude-dir=__pycache__"]
    if use_regex:
        cmd.append("-E")
    else:
        cmd.append("-F")
    if context > 0 and output_mode == "content":
        cmd.extend(["-C", str(context)])
    if output_mode == "files_only":
        cmd.append("-l")
    elif output_mode == "count":
        cmd.append("-c")
    if glob_pat:
        cmd.extend(["--include", glob_pat])
    cmd.extend(["--", pattern, str(search_root)])
    code, stdout = _run(cmd, cwd=root)
    if code == 124:
        return "Error: search timed out"
    # grep: 0=match, 1=none, 2=error (bad regex)
    if code == 2:
        msg = (stdout or "").strip()
        if "regex" in msg.casefold() or "invalid" in msg.casefold():
            return f"Error: invalid regex: {msg[:300]}"
        return None
    if code not in (0, 1):
        return None
    return _format_rg_output(
        root, stdout, output_mode=output_mode, context=context, limit=limit, offset=offset
    )


def _content_python(
    root: Path,
    search_root: Path,
    pattern: str,
    glob_pat: str | None,
    use_regex: bool,
    output_mode: str,
    context: int,
    limit: int,
    offset: int,
) -> str:
    cre = None
    if use_regex:
        try:
            cre = re.compile(pattern)
        except re.error as exc:
            return f"Error: invalid regex: {exc}"

    matches: list[str] = []
    file_counts: dict[str, int] = {}
    files_only: list[str] = []
    skipped = 0

    for path in _iter_files(search_root, root, glob_pat):
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        rel = path.relative_to(root).as_posix()
        lines = text.splitlines()
        hit_lines: list[int] = []
        for lineno, line in enumerate(lines, start=1):
            hit = cre.search(line) is not None if cre else (pattern in line)
            if hit:
                hit_lines.append(lineno)
        if not hit_lines:
            continue
        file_counts[rel] = len(hit_lines)
        if output_mode == "count":
            continue
        if output_mode == "files_only":
            if skipped < offset:
                skipped += 1
                continue
            files_only.append(rel)
            if len(files_only) >= limit:
                break
            continue
        for lineno in hit_lines:
            if skipped < offset:
                skipped += 1
                continue
            if context > 0:
                lo = max(1, lineno - context)
                hi = min(len(lines), lineno + context)
                block = []
                for j in range(lo, hi + 1):
                    mark = ">" if j == lineno else " "
                    sn = lines[j - 1]
                    if len(sn) > _MAX_SNIPPET:
                        sn = sn[:_MAX_SNIPPET] + "…"
                    block.append(f"{rel}:{j}:{mark}{sn}")
                matches.append("\n".join(block))
            else:
                sn = lines[lineno - 1].strip()
                if len(sn) > _MAX_SNIPPET:
                    sn = sn[:_MAX_SNIPPET] + "…"
                matches.append(f"{rel}:{lineno}:{sn}")
            if len(matches) >= limit:
                break
        if len(matches) >= limit:
            break

    if output_mode == "count":
        if not file_counts:
            return f"No matches for {pattern!r}"
        total = sum(file_counts.values())
        body = "\n".join(f"{p}: {n}" for p, n in sorted(file_counts.items())[:limit])
        return f"{total} match(es) in {len(file_counts)} file(s)\n{body}"
    if output_mode == "files_only":
        if not files_only:
            return f"No matches for {pattern!r}"
        return f"{len(files_only)} file(s)\n" + "\n".join(files_only)
    if not matches:
        return f"No matches for {pattern!r}"
    header = f"{len(matches)} match(es)"
    if len(matches) >= limit:
        header += f" (capped at {limit})"
    return header + "\n" + "\n".join(matches)


# ── filename search ──────────────────────────────────────────────────────


def _search_files_by_name(
    *,
    root: Path,
    search_root: Path,
    pattern: str,
    use_regex: bool,
    limit: int,
    offset: int,
) -> str:
    if use_regex:
        return _filenames_python(
            root, search_root, pattern, use_regex=True, limit=limit, offset=offset
        )
    if _has_cmd("rg"):
        out = _filenames_rg(root, search_root, pattern, limit, offset)
        if out is not None:
            return out
    if _has_cmd("find") and not _is_filesystem_root(search_root):
        out = _filenames_find(root, search_root, pattern, limit, offset)
        if out is not None:
            return out
    return _filenames_python(
        root, search_root, pattern, use_regex=False, limit=limit, offset=offset
    )


def _filename_glob(pattern: str) -> str:
    # Bare names become *name* for depth matching
    if "/" not in pattern and not any(c in pattern for c in "*?["):
        return f"*{pattern}*"
    return pattern


def _filenames_rg(
    root: Path, search_root: Path, pattern: str, limit: int, offset: int
) -> str | None:
    """List files via ``rg --files``.

    Never use ``--sortr`` (hangs on large trees like workplace root ``/``).
    Stream stdout and stop after ``limit+offset`` lines so listing ``/`` is fast.
    """
    import time

    glob_pattern = _filename_glob(pattern)
    cmd = [
        "rg",
        "--files",
        "--color",
        "never",
        "--line-buffered",  # critical: without this, pipe is fully buffered → hangs on /
        "--glob",
        glob_pattern,
        "--glob",
        "!**/.git/**",
        "--glob",
        "!**/node_modules/**",
        "--glob",
        "!**/.venv/**",
        str(search_root),
    ]
    fetch = limit + offset
    timeout = min(_TIMEOUT, 12)
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(root),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
            env={**os.environ, "TERM": "dumb"},
        )
    except OSError:
        return None

    files: list[str] = []
    deadline = time.monotonic() + timeout
    try:
        assert proc.stdout is not None
        while len(files) < fetch:
            if time.monotonic() > deadline:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    proc.kill()
                break
            line = proc.stdout.readline()
            if not line:
                break
            line = line.strip()
            if line:
                files.append(line)
        # Stop rg once we have enough (don't wait for full tree walk).
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            try:
                proc.kill()
            except OSError:
                pass
        try:
            proc.wait(timeout=2)
        except Exception:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except Exception:
                pass
    except Exception:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except Exception:
            pass
        return None

    page = files[offset : offset + limit]
    if not page:
        return f"No files matching {pattern!r}"
    rels = []
    for f in page:
        rel = _rel(root, f)
        try:
            p = Path(f) if os.path.isabs(f) else (root / f)
            size = p.stat().st_size
        except OSError:
            size = -1
        rels.append(f"{rel}  ({size} B)" if size >= 0 else rel)
    capped = len(files) >= fetch
    header = (
        f"{len(rels)} file(s) (capped at {limit}; via rg)"
        if capped
        else f"{len(rels)} file(s) (via rg)"
    )
    return header + "\n" + "\n".join(rels)


def _filenames_find(
    root: Path, search_root: Path, pattern: str, limit: int, offset: int
) -> str | None:
    name = pattern
    if "/" not in pattern and not any(c in pattern for c in "*?["):
        name = f"*{pattern}*"
    cmd = [
        "find",
        str(search_root),
        "-type",
        "f",
        "-name",
        name,
    ]
    code, stdout = _run(cmd, cwd=root)
    if code not in (0, 1):
        return None
    files = [ln for ln in stdout.splitlines() if ln.strip()]
    # Filter hidden segments
    clean = []
    for f in files:
        parts = Path(f).parts
        if any(p.startswith(".") and p not in {".", ".."} for p in parts):
            if not any(p.startswith(".") for p in search_root.parts):
                continue
        clean.append(f)
    page = clean[offset : offset + limit]
    if not page:
        return f"No files matching {pattern!r}"
    rels = [_rel(root, f) for f in page]
    header = f"{len(rels)} file(s) (via find)"
    return header + "\n" + "\n".join(rels)


def _filenames_python(
    root: Path,
    search_root: Path,
    pattern: str,
    *,
    use_regex: bool,
    limit: int,
    offset: int,
) -> str:
    cre = None
    if use_regex:
        try:
            cre = re.compile(pattern)
        except re.error as exc:
            return f"Error: invalid regex: {exc}"
    hits: list[str] = []
    skipped = 0
    try:
        for path in sorted(search_root.rglob("*")):
            if not path.is_file():
                continue
            if any(part in _SKIP_DIRS for part in path.parts):
                continue
            try:
                rel = path.relative_to(root).as_posix()
            except ValueError:
                continue
            name = path.name
            if cre is not None:
                ok = cre.search(name) is not None or cre.search(rel) is not None
            else:
                g = _filename_glob(pattern)
                ok = fnmatch.fnmatch(name, g) or fnmatch.fnmatch(rel, g)
                if not ok and "*" not in pattern and "?" not in pattern:
                    ok = pattern.casefold() in name.casefold()
            if not ok:
                continue
            if skipped < offset:
                skipped += 1
                continue
            try:
                size = path.stat().st_size
            except OSError:
                size = -1
            hits.append(f"{rel}  ({size} B)" if size >= 0 else rel)
            if len(hits) >= limit:
                break
    except OSError as exc:
        return f"Error: could not search files: {exc}"
    if not hits:
        return f"No files matching {pattern!r}"
    return f"{len(hits)} file(s)\n" + "\n".join(hits)


def _iter_files(search_root: Path, jail_root: Path, glob_pat: str | None):
    for path in sorted(search_root.rglob("*")):
        if not path.is_file():
            continue
        try:
            path.relative_to(jail_root)
        except ValueError:
            continue
        if any(part in _SKIP_DIRS for part in path.parts):
            continue
        if glob_pat and not fnmatch.fnmatch(path.name, glob_pat):
            continue
        yield path


__all__ = ["run"]
