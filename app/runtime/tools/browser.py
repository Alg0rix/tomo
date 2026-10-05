"""browser tool — supervised document rendering (offline + scoped online).

Offline sources (raw ``html``, ``data:`` URLs, owned workplace files) always
work with no internet: they render inside the caller's per-chat restricted
container. Online ``http(s)`` URLs additionally require scoped egress
(``network_egress=scoped``) and pass the browser SSRF boundary on the
initial URL and every redirect hop; the fetched document is then rendered
offline. Subresources and websockets are never retrieved live.
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

_DEFAULT_TIMEOUT = 60.0
_MAX_TIMEOUT = 120.0
_MAX_OUTPUT = 20_000


def _timeout_seconds(raw: Any) -> float:
    try:
        value = float(raw) if raw is not None else _DEFAULT_TIMEOUT
    except (TypeError, ValueError):
        value = _DEFAULT_TIMEOUT
    if value <= 0:
        return _DEFAULT_TIMEOUT
    return min(value, _MAX_TIMEOUT)


def _clip(text: str) -> str:
    if len(text) <= _MAX_OUTPUT:
        return text
    return text[:_MAX_OUTPUT] + f"\n...[truncated, {len(text)} chars total]"


def _decode_data_url(url: str) -> str | None:
    try:
        _, _, rest = url.partition(",")
        head, _, _ = url.partition(",")
        if ";base64" in head.split(";"):
            return base64.b64decode(rest, validate=False).decode("utf-8", errors="replace")
        from urllib.parse import unquote

        return unquote(rest)
    except (ValueError, base64.binascii.Error):
        return None


def _owned_file_html(source: str) -> tuple[str | None, str | None]:
    """``(html, error)`` for an owned workplace file (restricted-jailed)."""
    from app.runtime.access import current_execution
    from app.runtime.tools.sandbox import current_agent_id, jail_path, resolve_work_root
    from app.runtime.tools.vision_analyze import _restricted_workplace_file
    from app.services import store as _store

    execution = _store.access.revalidate(current_execution())
    if execution.execution_mode == "unrestricted":
        root = resolve_work_root(current_agent_id())
        resolved = jail_path(root, source)
        if isinstance(resolved, str):
            return None, resolved
    else:
        resource = next(
            (r for r in execution.resources if r.workplace_id == execution.active_workplace_id),
            None,
        )
        if resource is None or resource.transfer_only or resource.kind != "local":
            return None, "Error: working location is unavailable for browser input"
        resolved = _restricted_workplace_file(Path(resource.root_path), source)
        if isinstance(resolved, str):
            return None, resolved
    target = Path(resolved)
    try:
        if not target.is_file():
            return None, f"Error: could not read {source}: not a file"
        if target.stat().st_size > 512 * 1024:
            return None, "Error: file too large for browser render (max 512 KiB)"
        raw = target.read_bytes()
    except OSError as exc:
        return None, f"Error: could not read {source}: {exc}"
    try:
        return raw.decode("utf-8"), None
    except UnicodeDecodeError:
        return None, "Error: browser renders HTML/text files only"


def run(arguments: dict[str, Any]) -> str:
    """Render a document and return its title + readable text."""
    if not isinstance(arguments, dict):
        return "Error: browser expects a dict of arguments"
    from app.runtime.policy import authorize_tool

    authorize_tool("browser", arguments)
    to = _timeout_seconds(arguments.get("timeout"))
    html: str | None = None
    source_note = "offline document"

    inline = arguments.get("html")
    url = arguments.get("url")
    file_arg = arguments.get("file") or arguments.get("path")
    provided = [bool(inline), bool(url), bool(file_arg)]
    if sum(1 for p in provided if p) != 1:
        return "Error: provide exactly one of 'html', 'url' or 'file'"
    if inline is not None:
        if not isinstance(inline, str) or not inline.strip():
            return "Error: 'html' must be a non-empty string"
        html = inline
    elif file_arg is not None:
        if not isinstance(file_arg, str) or not file_arg.strip():
            return "Error: 'file' must be a non-empty path"
        html, err = _owned_file_html(file_arg.strip())
        if err:
            return err
        source_note = f"file {file_arg.strip()}"
        assert html is not None
    else:
        if not isinstance(url, str) or not url.strip():
            return "Error: 'url' must be a non-empty string"
        url = url.strip()
        scheme = urlparse(url).scheme.lower()
        if scheme == "data":
            html = _decode_data_url(url)
            if html is None or not html.strip():
                return "Error: could not decode data URL"
        elif scheme in {"http", "https"}:
            from app.runtime.browser.fetch import check_browser_url, fetch_document
            from app.runtime.net_policy import require_egress

            try:
                require_egress("browser")
            except Exception as exc:
                return f"Error: {exc}"
            err = check_browser_url(url)
            if err:
                return err
            fetched = fetch_document(url)
            if isinstance(fetched, str):
                return fetched
            body, final_url = fetched
            try:
                html = body.decode("utf-8", errors="replace")
            except Exception:
                return "Error: could not decode fetched document"
            if not html.strip():
                return "(empty response)"
            source_note = f"fetched {final_url}"
        else:
            return "Error: browser url must be data:, http: or https: (ws(s) live sockets are unavailable)"

    from app.runtime.access import current_execution
    from app.runtime.browser.offline import render_offline_html
    from app.services import store as _store

    context = _store.access.revalidate(current_execution())
    try:
        rendered = render_offline_html(context, html, timeout=to)
    except Exception as exc:
        return f"Error: {exc}"
    title = rendered.get("title") or "(no title)"
    text = rendered.get("text") or "(no readable text)"
    header = f"[{source_note}] {title}"
    # The render is a single offline document: subresources and websockets
    # were not retrieved live (container has no network).
    return f"{header}\n{_clip(text)}\n[subresources/websockets not executed live]"


__all__ = ["run"]
