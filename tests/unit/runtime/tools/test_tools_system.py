"""Consolidated tests (merged from: test_bash.py, test_artifacts.py, test_web_search.py).
- test_bash.py: bash tool: sandbox cwd, timeout, and error-string contract.
- test_artifacts.py: Tests for session-scoped filesystem artifacts + tools.
- test_web_search.py: web_search tool tests (httpx2 mocked).
"""

from __future__ import annotations

import time
from pathlib import Path
import pytest
from app.core import home
from app.runtime.tools import bash, progress, sandbox
from app.runtime.tools.registry import execute, get_openai_tools, reset_registry
from app.services import store
from app.workplaces.hub import hub
import json
import app.core.config as config
from app.runtime.artifacts.fs import (
    bind_session,
    category_for,
    delete_artifact_file,
    list_artifact_files,
    reset_session,
    write_artifact_text,
)
from app.runtime.tools.registry import execute, reset_registry
from app.runtime.tools.sandbox import bind_agent, reset_agent
from unittest.mock import MagicMock, patch
import httpx2
from app.runtime.tools import web_search


# --- from test_bash.py ---
@pytest.fixture(autouse=True)
def _reset(tmp_path: Path) -> None:
    store.rebind(tmp_path / "bash.db")
    hub.reset()
    # Ensure ops has no remote workplace so bash stays local.
    try:
        store.update_agent("ops", {"workplace_id": ""})
    except Exception:
        pass
    reset_registry()
    sandbox.reset_agent()
    yield
    sandbox.reset_agent()
    hub.reset()
    reset_registry()


def test_bash_schema_loaded() -> None:
    schema = next(t for t in get_openai_tools() if t["function"]["name"] == "bash")
    assert "command" in schema["function"]["parameters"]["properties"]


def test_bash_echo_in_work_dir() -> None:
    work = home.agent_work_dir("ops")
    work.mkdir(parents=True, exist_ok=True)
    sandbox.bind_agent("ops")
    result = execute("bash", {"command": "pwd && echo hello-tomo"})
    assert "hello-tomo" in result
    assert str(work.resolve()) in result


def test_bash_missing_command_is_error() -> None:
    assert execute("bash", {}).startswith("Error")


def test_bash_timeout_is_error_string() -> None:
    sandbox.bind_agent("ops")
    result = bash.run({"command": "sleep 5", "timeout": 0.2})
    assert result.startswith("Error")
    assert "timed out" in result.lower()


def test_bash_streams_output_before_exit() -> None:
    sandbox.bind_agent("ops")
    seen: list[tuple[float, str]] = []
    token = progress.bind(lambda chunk: seen.append((time.monotonic(), chunk)))
    try:
        started = time.monotonic()
        result = bash.run({"command": "echo first; sleep 0.6; echo second >&2"})
        finished = time.monotonic()
    finally:
        progress.reset(token)
    assert result == "first\nstderr:\nsecond"
    assert "".join(c for _, c in seen) == "first\nsecond\n"
    first_at = next(ts for ts, c in seen if "first" in c)
    assert first_at - started < 0.4 < finished - started


def test_bash_nonzero_exit_includes_code() -> None:
    sandbox.bind_agent("ops")
    result = execute("bash", {"command": "exit 7"})
    assert "7" in result


def test_bash_run_never_raises_on_bad_args() -> None:
    try:
        result = bash.run("not a dict")  # type: ignore[arg-type]
    except Exception as exc:  # pragma: no cover
        raise AssertionError(f"run raised: {exc!r}") from exc
    assert result.startswith("Error")


# --- from test_artifacts.py ---
def _rebind(tmp_path: Path, monkeypatch) -> None:
    reset_registry()
    store.rebind(tmp_path / "tomo.db")
    home_root = tmp_path / "home"
    work = tmp_path / "work"
    home_root.mkdir()
    work.mkdir()
    monkeypatch.setattr(config, "TOMO_HOME", home_root)
    monkeypatch.setattr(config, "TOMO_WORK", work)


def test_save_artifact_content_writes_file(tmp_path: Path, monkeypatch) -> None:
    _rebind(tmp_path, monkeypatch)
    token = bind_agent("main")
    sid = bind_session("sess_demo")
    try:
        out = execute(
            "save_artifact",
            {"filename": "report.md", "content": "# Hello\n"},
        )
        assert not out.startswith("Error"), out
        data = json.loads(out)
        assert data["filename"] == "report.md"
        assert data["session_id"] == "sess_demo"
        path = Path(data["filepath"])
        assert path.is_file()
        assert "sessions/sess_demo/artifacts" in str(path).replace("\\", "/")
        listed = list_artifact_files("sess_demo")
        assert listed["total"] == 1
    finally:
        reset_session(sid)
        reset_agent(token)


def test_artifacts_isolated_per_session(tmp_path: Path, monkeypatch) -> None:
    _rebind(tmp_path, monkeypatch)
    write_artifact_text("sess_a", "a.txt", "aaa")
    write_artifact_text("sess_b", "b.txt", "bbb")
    assert list_artifact_files("sess_a")["total"] == 1
    assert list_artifact_files("sess_a")["files"][0]["filename"] == "a.txt"
    assert list_artifact_files("sess_b")["files"][0]["filename"] == "b.txt"


def test_save_artifact_source_path_moves_local_file(tmp_path: Path, monkeypatch) -> None:
    _rebind(tmp_path, monkeypatch)
    token = bind_agent("main")
    sid = bind_session("sess_move")
    try:
        work = home.agent_work_dir("main")
        work.mkdir(parents=True, exist_ok=True)
        src = work / "out.csv"
        src.write_text("a,b\n1,2\n", encoding="utf-8")
        out = execute(
            "save_artifact",
            {"filename": "pricing.csv", "source_path": "out.csv"},
        )
        assert not out.startswith("Error"), out
        data = json.loads(out)
        dest = Path(data["filepath"])
        assert dest.is_file()
        assert not src.exists()
    finally:
        reset_session(sid)
        reset_agent(token)


def test_list_and_fetch_artifact(tmp_path: Path, monkeypatch) -> None:
    _rebind(tmp_path, monkeypatch)
    token = bind_agent("coder")
    sid = bind_session("sess_fetch")
    try:
        write_artifact_text("sess_fetch", "notes.txt", "keep me")
        listed = execute("list_artifacts", {"filter": "notes"})
        data = json.loads(listed)
        assert data["total"] >= 1
        fetched = execute("fetch_artifact", {"filename": "notes.txt"})
        info = json.loads(fetched)
        assert info["filename"] == "notes.txt"
        assert info["session_id"] == "sess_fetch"
    finally:
        reset_session(sid)
        reset_agent(token)


def test_save_requires_session(tmp_path: Path, monkeypatch) -> None:
    _rebind(tmp_path, monkeypatch)
    token = bind_agent("main")
    try:
        out = execute(
            "save_artifact",
            {"filename": "x.md", "content": "no session"},
        )
        assert out.startswith("Error")
        assert "session" in out.lower()
    finally:
        reset_agent(token)


def test_legacy_catalog_save_artifact(tmp_path: Path, monkeypatch) -> None:
    _rebind(tmp_path, monkeypatch)
    out = execute(
        "save_artifact",
        {"title": "Report", "path": "/tmp/out.md", "kind": "report"},
    )
    assert out.startswith("Saved artifact")
    arts = store.search_artifacts("Report")
    assert arts and arts[0]["path"] == "/tmp/out.md"


def test_delete_artifact_file(tmp_path: Path, monkeypatch) -> None:
    _rebind(tmp_path, monkeypatch)
    write_artifact_text("sess_del", "gone.txt", "x")
    assert delete_artifact_file("sess_del", "gone.txt")
    assert list_artifact_files("sess_del")["total"] == 0


def test_artifacts_enabled_gates_tools(tmp_path: Path, monkeypatch) -> None:
    _rebind(tmp_path, monkeypatch)
    store.create_agent({"id": "arty", "name": "Arty", "artifacts_enabled": True})
    ids = store.get_enabled_tool_ids("arty")
    assert "save_artifact" in ids
    store.update_agent("arty", {"artifacts_enabled": False})
    ids2 = store.get_enabled_tool_ids("arty")
    assert "save_artifact" not in ids2


def test_category_for_html() -> None:
    assert category_for("dashboard.html") == "html"
    assert category_for("index.HTM") == "html"
    assert category_for("notes.md") == "markdown"
    assert category_for("data.csv") == "csv"
    assert category_for("payload.json") == "json"
    assert category_for("app.py") == "code"
    assert category_for("report.pdf") == "pdf"


# --- from test_web_search.py ---
@pytest.fixture(autouse=True)
def _reset_web_search() -> None:
    reset_registry()
    yield
    reset_registry()


def _client_with_responses(*responses: MagicMock) -> MagicMock:
    mock_client = MagicMock()
    mock_client.__enter__.return_value = mock_client
    mock_client.__exit__.return_value = False
    mock_client.get.side_effect = list(responses)
    return mock_client


def test_web_search_formats_html_results() -> None:
    html = """
    <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fpython.org">Python</a>
    <a class="result__snippet">A programming language.</a>
    <a class="result__a" href="https://psf.org">Python Software Foundation</a>
    <a class="result__snippet">The PSF home.</a>
    """
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.text = html
    mock_client = _client_with_responses(mock_resp)

    with patch("app.runtime.tools.web_search.httpx2.Client", return_value=mock_client):
        result = execute("web_search", {"query": "python"})
    assert "1. Python" in result
    assert "A programming language." in result
    assert "https://python.org" in result
    assert "Python Software Foundation" in result


def test_web_search_empty_query_is_error() -> None:
    assert execute("web_search", {"query": "  "}).startswith("Error")


def test_web_search_request_failure() -> None:
    mock_client = MagicMock()
    mock_client.__enter__.return_value = mock_client
    mock_client.__exit__.return_value = False
    mock_client.get.side_effect = httpx2.TimeoutException("slow")

    with patch("app.runtime.tools.web_search.httpx2.Client", return_value=mock_client):
        result = execute("web_search", {"query": "python"})
    assert result.startswith("Error")


def test_web_search_no_results() -> None:
    html_resp = MagicMock()
    html_resp.raise_for_status = MagicMock()
    html_resp.text = "<html><body>no hits</body></html>"

    ia_resp = MagicMock()
    ia_resp.raise_for_status = MagicMock()
    ia_resp.text = '{"Heading":"","AbstractText":"","RelatedTopics":[]}'
    ia_resp.json.return_value = {
        "Heading": "",
        "AbstractText": "",
        "RelatedTopics": [],
    }

    mock_client = _client_with_responses(html_resp, ia_resp)

    with patch("app.runtime.tools.web_search.httpx2.Client", return_value=mock_client):
        result = execute("web_search", {"query": "zzzz"})
    assert result.startswith("No results")


def test_web_search_empty_ia_body_falls_through() -> None:
    """Instant Answer empty body must not raise JSON decode errors."""
    html_resp = MagicMock()
    html_resp.raise_for_status = MagicMock()
    html_resp.text = ""

    ia_resp = MagicMock()
    ia_resp.raise_for_status = MagicMock()
    ia_resp.text = ""
    ia_resp.json.side_effect = ValueError(
        "Expecting value: line 1 column 1 (char 0)"
    )

    mock_client = _client_with_responses(html_resp, ia_resp)

    with patch("app.runtime.tools.web_search.httpx2.Client", return_value=mock_client):
        result = execute(
            "web_search",
            {
                "query": "Bank BCA developer API mutasi rekening statement transaction API"
            },
        )
    assert result.startswith("No results")
    assert "Expecting value" not in result


def test_web_search_falls_back_to_instant_answer() -> None:
    html_resp = MagicMock()
    html_resp.raise_for_status = MagicMock()
    html_resp.text = "<html></html>"

    ia_resp = MagicMock()
    ia_resp.raise_for_status = MagicMock()
    ia_resp.text = '{"ok":true}'
    ia_resp.json.return_value = {
        "Heading": "Python",
        "AbstractText": "A programming language.",
        "AbstractURL": "https://python.org",
        "RelatedTopics": [],
    }

    mock_client = _client_with_responses(html_resp, ia_resp)

    with patch("app.runtime.tools.web_search.httpx2.Client", return_value=mock_client):
        result = execute("web_search", {"query": "python"})
    assert "1. Python" in result
    assert "A programming language." in result


def test_web_search_overall_timeout_unsticks_hung_request(monkeypatch) -> None:
    """A request stuck below httpx2 timeouts (e.g. getaddrinfo) must not hang the tool."""
    import time

    monkeypatch.setattr(web_search, "_OVERALL_TIMEOUT", 0.5)

    mock_client = MagicMock()
    mock_client.__enter__.return_value = mock_client
    mock_client.__exit__.return_value = False

    def hang(*_a, **_k):
        time.sleep(2)
        return MagicMock()

    mock_client.get.side_effect = hang

    with patch("app.runtime.tools.web_search.httpx2.Client", return_value=mock_client):
        t0 = time.monotonic()
        result = execute("web_search", {"query": "python"})
        elapsed = time.monotonic() - t0
    assert result.startswith("Error")
    assert "timed out" in result.lower()
    assert elapsed < 1.5


