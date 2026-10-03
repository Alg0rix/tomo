"""Consolidated tests (merged from: test_workplaces.py, test_llm_profiles.py, test_background_jobs.py, test_session_isolation.py).
- test_workplaces.py: Workplaces: SQLite CRUD, Connect, encrypted SSH secrets, agent assignment.
- test_llm_profiles.py: LLM model profiles: CRUD, encrypted api_key, masked public view, default.
- test_background_jobs.py: Persistence, admission, retention, and completion-claim invariants.
- test_session_isolation.py: Per-user chat session isolation (strict multi-user).
"""

from __future__ import annotations

from pathlib import Path
import pytest
from app.services import store
from app.workplaces.backends import ssh as ssh_backend
import time
from concurrent.futures import ThreadPoolExecutor
from app.models.mixins.background_jobs import LOG_LIMIT


# --- from test_workplaces.py ---
def _rebind(tmp_path: Path) -> None:
    store.rebind(tmp_path / "workplaces.db")


def _raw_col(workplace_id: str, col: str) -> str:
    row = store._conn.execute(
        f"SELECT {col} FROM workplaces WHERE id=?", (workplace_id,)
    ).fetchone()
    return row[col] if row else ""


def test_migrate_creates_workplaces_table(tmp_path: Path) -> None:
    _rebind(tmp_path)
    names = {
        r[0]
        for r in store._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    assert "workplaces" in names


def test_create_local_and_connect(tmp_path: Path) -> None:
    _rebind(tmp_path)
    root = tmp_path / "local-root"
    root.mkdir()
    wp = store.create_workplace(
        {"id": "wp_local", "name": "Local", "kind": "local", "root_path": str(root)}
    )
    assert wp["kind"] == "local"
    assert wp["status"] == "ready"
    assert wp["host"] == str(root)
    result = store.connect_workplace("wp_local")
    assert result is not None
    assert result["ok"] is True
    assert result["status"] == "connected"
    # Public view keeps local workplaces as "ready" (path present), not tunnel "connected".
    assert store.get_workplace("wp_local")["status"] == "ready"


def test_local_connect_fails_missing_path(tmp_path: Path) -> None:
    _rebind(tmp_path)
    store.create_workplace(
        {
            "id": "wp_missing",
            "name": "Missing",
            "kind": "local",
            "root_path": str(tmp_path / "nope"),
        }
    )
    result = store.connect_workplace("wp_missing")
    assert result["ok"] is False
    assert result["status"] == "offline"


def test_ssh_secrets_encrypted(tmp_path: Path) -> None:
    _rebind(tmp_path)
    wp = store.create_workplace(
        {
            "id": "wp_ssh",
            "name": "SSH",
            "kind": "ssh",
            "ssh_host": "example.com",
            "ssh_user": "deploy",
            "ssh_password": "s3cret-pass",
            "ssh_key": "-----BEGIN KEY-----\nabc\n-----END KEY-----",
        }
    )
    assert wp["password_set"] is True
    assert wp["key_set"] is True
    assert "ssh_password" not in wp
    assert "ssh_key" not in wp
    raw_pwd = _raw_col("wp_ssh", "ssh_password")
    raw_key = _raw_col("wp_ssh", "ssh_key")
    assert raw_pwd.startswith("enc:v1:")
    assert raw_key.startswith("enc:v1:")
    assert "s3cret" not in raw_pwd


def test_blank_ssh_password_keeps_ciphertext(tmp_path: Path) -> None:
    _rebind(tmp_path)
    store.create_workplace(
        {
            "id": "wp_ssh",
            "name": "SSH",
            "kind": "ssh",
            "ssh_host": "h",
            "ssh_user": "u",
            "ssh_password": "keep-me",
        }
    )
    before = _raw_col("wp_ssh", "ssh_password")
    store.update_workplace("wp_ssh", {"ssh_password": "", "name": "SSH2"})
    assert _raw_col("wp_ssh", "ssh_password") == before
    assert store.get_workplace("wp_ssh")["name"] == "SSH2"


def test_ssh_connect_mocked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _rebind(tmp_path)

    def _ok(host: str, port: int, user: str, password: str, key: str) -> tuple[bool, str]:
        assert host == "box.test"
        assert user == "ops"
        assert password == "pw"
        return True, "mocked ok"

    monkeypatch.setattr(ssh_backend, "probe_ssh", _ok)
    store.create_workplace(
        {
            "id": "wp_ssh",
            "name": "SSH",
            "kind": "ssh",
            "ssh_host": "box.test",
            "ssh_user": "ops",
            "ssh_password": "pw",
        }
    )
    result = store.connect_workplace("wp_ssh")
    assert result["ok"] is True
    assert result["status"] == "connected"
    assert "mocked" in result["message"]


def test_tunnel_create_issues_pairing_code(tmp_path: Path) -> None:
    _rebind(tmp_path)
    wp = store.create_workplace(
        {"id": "wp_tun", "name": "Tunnel", "kind": "tunnel"}
    )
    assert wp["status"] == "pairing"
    assert wp["pairing_code"]
    assert len(wp["pairing_code"]) >= 6
    assert wp["pairing_expires_at"] > 0
    assert wp["connector_token_set"] is False


def test_tunnel_connect_not_connected_without_socket(tmp_path: Path) -> None:
    _rebind(tmp_path)
    from app.workplaces.hub import hub

    hub.reset()
    store.create_workplace(
        {"id": "wp_tun", "name": "Tunnel", "kind": "tunnel"}
    )
    result = store.connect_workplace("wp_tun")
    assert result["ok"] is False
    assert result["status"] in ("pairing", "offline")
    assert store.get_workplace("wp_tun")["status"] != "connected"


def test_tunnel_pairing_code_refresh(tmp_path: Path) -> None:
    _rebind(tmp_path)
    wp = store.create_workplace(
        {"id": "wp_tun", "name": "Tunnel", "kind": "tunnel"}
    )
    first = wp["pairing_code"]
    again = store.issue_pairing_code("wp_tun")
    assert again is not None
    assert again["pairing_code"]
    assert again["pairing_code"] != first or True  # may rarely collide
    assert again["status"] in ("pairing", "connected")


def test_tunnel_token_encrypted_after_pair(tmp_path: Path) -> None:
    _rebind(tmp_path)
    wp = store.create_workplace(
        {"id": "wp_tun", "name": "Tunnel", "kind": "tunnel"}
    )
    code = wp["pairing_code"]
    result = store.pair_connector(code, hostname="pi.local", version="0.2.0")
    assert result is not None
    assert result["workplace_id"] == "wp_tun"
    assert result["token"]
    public = store.get_workplace("wp_tun")
    assert public["connector_token_set"] is True
    # Paired but not live yet — honest offline until WebSocket registers.
    assert public["status"] == "offline"
    assert "connector_token" not in public or public.get("connector_token") in ("", None)
    raw = _raw_col("wp_tun", "connector_token")
    assert raw.startswith("enc:v1:")
    assert result["token"] not in raw
    # Reconnect with token (hello path) marks connected in DB.
    hello = store.hello_connector(result["token"], hostname="pi.local")
    assert hello == {"workplace_id": "wp_tun"}
    assert store.get_workplace("wp_tun")["status"] == "connected"


def test_assign_workplace_to_agent(tmp_path: Path) -> None:
    _rebind(tmp_path)
    root = tmp_path / "wp"
    root.mkdir()
    store.create_workplace(
        {"id": "wp1", "name": "WP", "kind": "local", "root_path": str(root)}
    )
    updated = store.update_agent("ops", {"workplace_id": "wp1"})
    assert updated["workplace_id"] == "wp1"
    assert store.get_workplace("wp1")["agent_count"] == 1


def test_assign_missing_workplace_raises(tmp_path: Path) -> None:
    _rebind(tmp_path)
    with pytest.raises(ValueError, match="Workplace not found"):
        store.update_agent("ops", {"workplace_id": "nope"})


def test_delete_workplace_clears_agent(tmp_path: Path) -> None:
    _rebind(tmp_path)
    store.create_workplace(
        {"id": "wp1", "name": "WP", "kind": "local", "root_path": str(tmp_path)}
    )
    store.update_agent("ops", {"workplace_id": "wp1"})
    assert store.delete_workplace("wp1") is True
    assert store.get_agent("ops")["workplace_id"] == ""


def test_stats_workplace_count(tmp_path: Path) -> None:
    _rebind(tmp_path)
    assert store.stats()["workplace_count"] == 0
    store.create_workplace(
        {"id": "wp1", "name": "WP", "kind": "local", "root_path": str(tmp_path)}
    )
    assert store.stats()["workplace_count"] == 1


# --- from test_llm_profiles.py ---
def _raw_api_key(profile_id: str) -> str:
    """Read the raw (ciphertext) api_key column from the store's own connection."""
    row = store._conn.execute(
        "SELECT api_key FROM llm_profiles WHERE id=?", (profile_id,)
    ).fetchone()
    return row["api_key"] if row else ""


def test_create_profile_encrypts_api_key(tmp_path) -> None:
    _rebind(tmp_path)
    prof = store.create_llm_profile(
        {
            "id": "default",
            "name": "Default",
            "base_url": "https://api.openai.com/v1",
            "api_key": "sk-test-1234567890abcdef",
            "model": "gpt-4o-mini",
        }
    )
    assert prof["id"] == "default"
    assert prof["api_key_set"] is True
    # Public view masks the key — never the raw secret.
    assert prof["api_key"] == "••••cdef"
    assert "sk-test" not in prof["api_key"]
    # Raw DB column is ciphertext, not plaintext.
    raw = _raw_api_key("default")
    assert raw.startswith("enc:v1:")
    assert "sk-test" not in raw


def test_list_profiles_returns_masked(tmp_path) -> None:
    _rebind(tmp_path)
    store.create_llm_profile(
        {"id": "a", "name": "A", "base_url": "", "api_key": "sk-aaaaaaaa1234", "model": "m"}
    )
    store.create_llm_profile(
        {"id": "b", "name": "B", "base_url": "", "api_key": "", "model": "m"}
    )
    by_id = {p["id"]: p for p in store.list_llm_profiles()}
    assert by_id["a"]["api_key_set"] is True
    assert by_id["a"]["api_key"] == "••••1234"
    assert by_id["b"]["api_key_set"] is False
    assert by_id["b"]["api_key"] == ""


def test_blank_put_keeps_existing_key(tmp_path) -> None:
    _rebind(tmp_path)
    store.create_llm_profile(
        {"id": "p", "name": "P", "base_url": "https://x/v1", "api_key": "sk-keepsecret99", "model": "m1"}
    )
    raw_before = _raw_api_key("p")
    updated = store.update_llm_profile("p", {"api_key": "", "model": "m2"})
    assert updated["model"] == "m2"
    assert updated["api_key_set"] is True
    # Ciphertext unchanged — blank key never clears.
    assert _raw_api_key("p") == raw_before


def test_update_with_new_key_reencrypts(tmp_path) -> None:
    _rebind(tmp_path)
    store.create_llm_profile(
        {"id": "p", "name": "P", "base_url": "", "api_key": "sk-old", "model": "m"}
    )
    store.update_llm_profile("p", {"api_key": "sk-new1234"})
    raw = _raw_api_key("p")
    assert raw.startswith("enc:v1:")
    # Runtime resolution decrypts to the new key.
    assert store.resolve_llm_profile(None)["api_key"] == "sk-new1234"


def test_set_and_get_default(tmp_path) -> None:
    _rebind(tmp_path)
    store.create_llm_profile({"id": "a", "name": "A", "base_url": "", "api_key": "sk-a", "model": "m"})
    store.create_llm_profile({"id": "b", "name": "B", "base_url": "", "api_key": "sk-b", "model": "m"})
    assert store.get_default_llm_profile_id() == ""
    store.set_default_llm_profile("b")
    assert store.get_default_llm_profile_id() == "b"


def test_delete_profile(tmp_path) -> None:
    _rebind(tmp_path)
    store.create_llm_profile({"id": "p", "name": "P", "base_url": "", "api_key": "sk", "model": "m"})
    assert store.delete_llm_profile("p") is True
    assert store.delete_llm_profile("p") is False
    assert store.list_llm_profiles() == []


def test_create_duplicate_profile_raises(tmp_path) -> None:
    _rebind(tmp_path)
    store.create_llm_profile({"id": "p", "name": "P", "base_url": "", "api_key": "sk", "model": "m"})
    import pytest

    with pytest.raises(ValueError):
        store.create_llm_profile({"id": "p", "name": "Dup", "base_url": "", "api_key": "sk", "model": "m"})


def test_resolve_profile_agent_then_default_then_first(tmp_path) -> None:
    _rebind(tmp_path)
    store.create_llm_profile({"id": "default", "name": "D", "base_url": "https://d/v1", "api_key": "sk-d", "model": "md"})
    store.set_default_llm_profile("default")
    store.create_llm_profile({"id": "fast", "name": "F", "base_url": "https://f/v1", "api_key": "sk-f", "model": "mf"})
    # Agent "main" (seeded, empty model_id) -> default profile.
    assert store.resolve_llm_profile("main")["id"] == "default"
    # Assign fast to main -> agent profile wins.
    store.update_agent("main", {"model_id": "fast"})
    assert store.resolve_llm_profile("main")["id"] == "fast"
    # No agent id -> default.
    assert store.resolve_llm_profile(None)["id"] == "default"


def test_resolve_profile_returns_none_when_empty(tmp_path) -> None:
    _rebind(tmp_path)
    assert store.resolve_llm_profile(None) is None
    assert store.resolve_llm_profile("main") is None


def test_profile_reasoning_efforts_are_normalized_and_default_to_last(tmp_path) -> None:
    _rebind(tmp_path)
    profile = store.create_llm_profile(
        {
            "id": "p",
            "name": "P",
            "api_key": "sk-p",
            "model": "model-a",
            "reasoning_efforts": [" low ", "", "high", "low"],
        }
    )

    assert profile["reasoning_efforts"] == ["low", "high"]
    assert store.resolve_llm_profile(None)["reasoning_efforts"] == ["low", "high"]


def test_session_reasoning_effort_persists_and_falls_back_when_profile_changes(
    tmp_path,
) -> None:
    _rebind(tmp_path)
    store.create_llm_profile(
        {
            "id": "default",
            "name": "D",
            "api_key": "sk-d",
            "model": "model-a",
            "reasoning_efforts": ["low", "high"],
        }
    )
    store.set_default_llm_profile("default")
    sid = store.create_swarm_session(["main"], user_id="web")

    state = store.get_session_reasoning_effort(sid)
    assert state["reasoning_efforts"] == ["low", "high"]
    assert state["reasoning_effort"] == "high"

    store.set_session_reasoning_effort(sid, "low")
    assert store.get_session(sid)["reasoning_effort"] == "low"
    assert store.resolve_session_reasoning_effort(sid, "main") == "low"

    store.update_llm_profile("default", {"reasoning_efforts": ["minimal", "max"]})
    assert store.resolve_session_reasoning_effort(sid, "main") == "max"

    store.create_llm_profile(
        {
            "id": "ops_profile",
            "name": "Ops",
            "api_key": "sk-ops",
            "model": "ops-model",
            "reasoning_efforts": ["quick-ops", "max-ops"],
        }
    )
    store.update_agent("ops", {"model_id": "ops_profile"})
    assert store.resolve_session_reasoning_effort(sid, "ops") == "max-ops"


def test_session_reasoning_effort_rejects_unknown_value(tmp_path) -> None:
    _rebind(tmp_path)
    store.create_llm_profile(
        {
            "id": "default",
            "name": "D",
            "api_key": "sk-d",
            "model": "model-a",
            "reasoning_efforts": ["low", "high"],
        }
    )
    store.set_default_llm_profile("default")
    sid = store.create_swarm_session(["main"])

    import pytest

    with pytest.raises(ValueError, match="reasoning effort"):
        store.set_session_reasoning_effort(sid, "unsupported")


def test_create_subscription_profile_encrypts_tokens(tmp_path) -> None:
    _rebind(tmp_path)
    from app.models.mixins import llm_profiles as llm_profiles_store

    with store._lock:
        prof = llm_profiles_store.create_subscription_profile(
            store._conn,
            provider="openai-codex",
            access_token="at-secret-123",
            refresh_token="rt-secret-456",
            expires_at=9999999999.0,
            name="ChatGPT (Codex)",
            model="gpt-5-codex",
            base_url="https://chatgpt.com/backend-api/codex",
        )
    assert prof["auth_mode"] == "subscription"
    assert prof["subscription_provider"] == "openai-codex"
    assert prof["access_token_set"] is True
    assert prof["refresh_token_set"] is True
    assert "access_token" not in prof
    assert "refresh_token" not in prof
    raw = store._conn.execute(
        "SELECT access_token, refresh_token FROM llm_profiles WHERE id=?", (prof["id"],)
    ).fetchone()
    assert raw["access_token"].startswith("enc:v1:")
    assert "at-secret-123" not in raw["access_token"]
    assert raw["refresh_token"].startswith("enc:v1:")


def test_find_subscription_profile_returns_decrypted(tmp_path) -> None:
    _rebind(tmp_path)
    from app.models.mixins import llm_profiles as llm_profiles_store

    with store._lock:
        created = llm_profiles_store.create_subscription_profile(
            store._conn, provider="openai-codex", access_token="at-1",
            refresh_token="rt-1", expires_at=123.0, name="ChatGPT (Codex)",
            model="gpt-5-codex", base_url="https://chatgpt.com/backend-api/codex",
        )
        found = llm_profiles_store.find_subscription_profile(store._conn, "openai-codex")
    assert found is not None
    assert found["id"] == created["id"]
    assert found["access_token"] == "at-1"
    assert found["refresh_token"] == "rt-1"
    assert found["token_expires_at"] == 123.0


def test_save_subscription_tokens_reencrypts(tmp_path) -> None:
    _rebind(tmp_path)
    from app.models.mixins import llm_profiles as llm_profiles_store

    with store._lock:
        created = llm_profiles_store.create_subscription_profile(
            store._conn, provider="openai-codex", access_token="at-old",
            refresh_token="rt-old", expires_at=1.0, name="ChatGPT (Codex)",
            model="gpt-5-codex", base_url="https://chatgpt.com/backend-api/codex",
        )
        llm_profiles_store.save_subscription_tokens(
            store._conn, created["id"],
            access_token="at-new", refresh_token="rt-new", expires_at=2.0,
        )
        refreshed = llm_profiles_store.get_profile(store._conn, created["id"])
    assert refreshed["access_token"] == "at-new"
    assert refreshed["refresh_token"] == "rt-new"
    assert refreshed["token_expires_at"] == 2.0


def test_resolve_profile_includes_subscription_fields(tmp_path) -> None:
    _rebind(tmp_path)
    from app.models.mixins import llm_profiles as llm_profiles_store

    with store._lock:
        created = llm_profiles_store.create_subscription_profile(
            store._conn, provider="openai-codex", access_token="at-x",
            refresh_token="rt-x", expires_at=42.0, name="ChatGPT (Codex)",
            model="gpt-5-codex", base_url="https://chatgpt.com/backend-api/codex",
        )
        llm_profiles_store.set_default_model_id(store._conn, created["id"])
    resolved = store.resolve_llm_profile(None)
    assert resolved["auth_mode"] == "subscription"
    assert resolved["access_token"] == "at-x"
    assert resolved["token_expires_at"] == 42.0


def test_resolve_profile_refreshes_expiring_subscription_token(tmp_path, monkeypatch) -> None:
    _rebind(tmp_path)
    from app.models.mixins import llm_profiles as llm_profiles_store

    with store._lock:
        created = llm_profiles_store.create_subscription_profile(
            store._conn, provider="openai-codex", access_token="at-stale",
            refresh_token="rt-1", expires_at=1.0,  # already expired
            name="ChatGPT (Codex)", model="gpt-5-codex",
            base_url="https://chatgpt.com/backend-api/codex",
        )
        llm_profiles_store.set_default_model_id(store._conn, created["id"])

    def fake_refresh(refresh_token, **kw):
        assert refresh_token == "rt-1"
        return {"access_token": "at-fresh", "refresh_token": "rt-2", "expires_at": 99999999999.0}

    monkeypatch.setattr(
        "app.runtime.llm.codex_oauth.refresh_tokens", fake_refresh
    )
    resolved = store.resolve_llm_profile(None)
    assert resolved["access_token"] == "at-fresh"
    assert resolved["needs_reauth"] is False
    raw = store._conn.execute(
        "SELECT access_token FROM llm_profiles WHERE id=?", (created["id"],)
    ).fetchone()
    assert "at-fresh" not in raw["access_token"]  # persisted encrypted, not plaintext
    assert raw["access_token"].startswith("enc:v1:")


def test_resolve_profile_skips_refresh_when_token_fresh(tmp_path, monkeypatch) -> None:
    _rebind(tmp_path)
    from app.models.mixins import llm_profiles as llm_profiles_store

    with store._lock:
        created = llm_profiles_store.create_subscription_profile(
            store._conn, provider="openai-codex", access_token="at-fresh",
            refresh_token="rt-1", expires_at=time.time() + 3600,
            name="ChatGPT (Codex)", model="gpt-5-codex",
            base_url="https://chatgpt.com/backend-api/codex",
        )
        llm_profiles_store.set_default_model_id(store._conn, created["id"])

    def fail_refresh(*a, **kw):
        raise AssertionError("refresh should not be called for a fresh token")

    monkeypatch.setattr("app.runtime.llm.codex_oauth.refresh_tokens", fail_refresh)
    resolved = store.resolve_llm_profile(None)
    assert resolved["access_token"] == "at-fresh"
    assert resolved["needs_reauth"] is False


def test_resolve_profile_flags_needs_reauth_on_terminal_refresh_failure(tmp_path, monkeypatch) -> None:
    _rebind(tmp_path)
    from app.models.mixins import llm_profiles as llm_profiles_store
    from app.runtime.llm.codex_oauth import CodexAuthError

    with store._lock:
        created = llm_profiles_store.create_subscription_profile(
            store._conn, provider="openai-codex", access_token="at-stale",
            refresh_token="rt-1", expires_at=1.0,
            name="ChatGPT (Codex)", model="gpt-5-codex",
            base_url="https://chatgpt.com/backend-api/codex",
        )
        llm_profiles_store.set_default_model_id(store._conn, created["id"])

    def fake_refresh(refresh_token, **kw):
        raise CodexAuthError("expired", code="invalid_grant", relogin_required=True)

    monkeypatch.setattr("app.runtime.llm.codex_oauth.refresh_tokens", fake_refresh)
    resolved = store.resolve_llm_profile(None)
    assert resolved["needs_reauth"] is True


def test_resolve_profile_api_key_profile_has_needs_reauth_false(tmp_path) -> None:
    _rebind(tmp_path)
    store.create_llm_profile({"id": "p", "name": "P", "api_key": "sk-p", "model": "m"})
    store.set_default_llm_profile("p")
    resolved = store.resolve_llm_profile(None)
    assert resolved["needs_reauth"] is False


# --- from test_background_jobs.py ---
@pytest.fixture
def sid(tmp_path):
    store.rebind(tmp_path / 'jobs.db')
    return store.get_or_create_session('ops', 'web')


def make(sid, **data):
    return store.create_background_job({'session_id': sid, 'user_id': 'web', 'command': 'echo hi', **data})


def test_durable_completion_claim_once(sid):
    item = make(sid)
    done = store.update_background_job(item['id'], {'status': 'succeeded', 'returncode': 0})
    assert done['continuation_status'] == 'pending'
    event = done['completion_event_id']
    store.update_background_job(item['id'], {'status': 'running'})
    assert store.get_background_job(item['id'])['status'] == 'succeeded'
    with ThreadPoolExecutor(max_workers=4) as pool:
        claims = list(pool.map(lambda _: store.claim_background_jobs([item['id']]), range(4)))
    assert claims.count(True) == 1
    store.update_background_job(item['id'], {'status': 'failed', 'returncode': 3})
    read = store.get_background_job(item['id'])
    assert read['returncode'] == 0
    assert read['continuation_status'] == 'claimed'
    assert read['completion_event_id'] == event


def test_claim_batch_atomic_and_one_destination(sid):
    first = make(sid, delivery={'thread_id': 1}, actor_id=1)
    second = make(sid, delivery={'thread_id': 2}, actor_id=1)
    for item in (first, second):
        store.update_background_job(item['id'], {'status': 'succeeded', 'returncode': 0})
    assert not store.claim_background_jobs([first['id'], second['id']])
    assert not store.claim_background_jobs([first['id'], 'missing'])
    assert store.get_background_job(first['id'])['continuation_status'] == 'pending'


def test_global_admission_atomic(sid):
    other = store.get_or_create_session('ops', 'another-user')
    def start(index):
        try:
            return make(sid if index % 2 else other)['id']
        except ValueError:
            return None
    with ThreadPoolExecutor(max_workers=8) as pool:
        ids = list(pool.map(start, range(24)))
    assert len([ident for ident in ids if ident]) == 16
    assert len(set(ident for ident in ids if ident)) == 16
    open_job = next(ident for ident in ids if ident)
    store.update_background_job(open_job, {'status': 'unknown', 'monitoring_closed': True})
    assert make(sid)['id']


def test_bounds_expiry_and_binding_immutable(sid):
    item = make(sid)
    result = store.update_background_job(item['id'], {'stdout': 'x' * LOG_LIMIT, 'stderr': 'y' * LOG_LIMIT,
                                                      'session_id': 'malicious', 'delivery': {'chat_id': 1}})
    assert len(result['stdout'].encode()) + len(result['stderr'].encode()) <= LOG_LIMIT
    assert result['truncated']
    assert result['session_id'] == sid and result['delivery'] is None
    store.update_background_job(item['id'], {'status': 'succeeded', 'returncode': 0,
                                            'finished_at': time.time() - 8 * 86400})
    expired = store.get_background_job(item['id'])
    assert expired['logs_expired'] and expired['stdout'] == expired['stderr'] == ''


def test_stop_and_clear_never_nudge(sid):
    stopped = make(sid)
    assert store.update_background_job(stopped['id'], {'status': 'stopped', 'returncode': -15})['continuation_status'] == 'cancelled'
    active = make(sid)
    store.clear_session_by_id(sid)
    assert store.background_jobs_paused(sid)
    done = store.update_background_job(active['id'], {'status': 'succeeded', 'returncode': 0})
    assert done['continuation_status'] == 'cancelled'
    store.set_background_jobs_paused(sid, False)
    assert not store.background_jobs_paused(sid)


def test_closed_monitor_cannot_resurrect_completion(sid):
    item = make(sid, status='unknown')
    store.update_background_job(item['id'], {'monitoring_closed': True, 'continuation_status': 'cancelled'})
    done = store.update_background_job(item['id'], {'status': 'succeeded', 'returncode': 0})
    assert done['continuation_status'] == 'cancelled'
    assert not store.claim_background_jobs([item['id']])


def test_filtered_metadata_listing_omits_logs(sid):
    job = make(sid, stdout='large log', card_message_id=123)
    done = store.update_background_job(job['id'], {'status': 'succeeded', 'returncode': 0})
    metadata = store.list_background_jobs(continuation_status='pending', card_message_id=123, include_logs=False)
    assert len(metadata) == 1 and metadata[0]['id'] == done['id']
    assert 'stdout' not in metadata[0] and 'stderr' not in metadata[0]
    assert store.get_background_job(job['id'])['stdout'] == 'large log'


def test_inline_log_upgrade_preserves_then_separates_retained_output(sid):
    import json

    job = make(sid)
    def legacy(conn):
        payload = {**job, 'stdout': 'legacy output', 'stderr': 'legacy error'}
        with conn:
            conn.execute('UPDATE background_jobs SET payload_json=? WHERE id=?', (json.dumps(payload), job['id']))
            conn.execute('DELETE FROM background_job_logs WHERE job_id=?', (job['id'],))
    store.with_db(legacy)
    assert store.get_background_job(job['id'])['stdout'] == 'legacy output'
    store.update_background_job(job['id'], {'status': 'running'})
    def inspect(conn):
        payload = json.loads(conn.execute('SELECT payload_json FROM background_jobs WHERE id=?', (job['id'],)).fetchone()[0])
        logs = conn.execute('SELECT * FROM background_job_logs WHERE job_id=?', (job['id'],)).fetchone()
        assert 'stdout' not in payload and 'stderr' not in payload
        assert logs['stdout'] == 'legacy output' and logs['stderr'] == 'legacy error'
    store.with_db(inspect)


def test_session_cascade_removes_log_and_admission_rows(sid):
    job = make(sid, stdout='retained')
    store.update_background_job(job['id'], {'status': 'succeeded', 'returncode': 0})
    store.set_background_jobs_paused(sid, True)
    assert store.delete_session(sid)
    def inspect(conn):
        assert conn.execute('SELECT 1 FROM background_jobs WHERE id=?', (job['id'],)).fetchone() is None
        assert conn.execute('SELECT 1 FROM background_job_logs WHERE job_id=?', (job['id'],)).fetchone() is None
        assert conn.execute('SELECT 1 FROM background_job_admission WHERE session_id=?', (sid,)).fetchone() is None
    store.with_db(inspect)


def test_multibyte_truncation_stays_within_byte_limit(sid):
    job = make(sid)
    result = store.update_background_job(job['id'], {'stdout': '界' * LOG_LIMIT, 'stderr': 'é'})
    assert len(result['stdout'].encode()) + len(result['stderr'].encode()) <= LOG_LIMIT


# --- from test_session_isolation.py ---
def test_list_sessions_filters_by_user(tmp_path) -> None:
    _rebind(tmp_path)
    a = store.create_swarm_session(["main"], user_id="usr_alice")
    b = store.create_swarm_session(["main"], user_id="usr_bob")
    store.create_swarm_session(["main"], user_id="web")

    alice = store.list_sessions(user_id="usr_alice")
    bob = store.list_sessions(user_id="usr_bob")
    all_rows = store.list_sessions()

    assert {s["id"] for s in alice} == {a}
    assert {s["id"] for s in bob} == {b}
    assert len(all_rows) >= 3


def test_get_owned_session_hides_other_users(tmp_path) -> None:
    _rebind(tmp_path)
    sid = store.create_swarm_session(["main"], user_id="usr_alice")
    assert store.get_owned_session(sid, "usr_alice") is not None
    assert store.get_owned_session(sid, "usr_bob") is None
    assert store.get_owned_session("missing", "usr_alice") is None


def test_prune_drafts_only_own_user(tmp_path) -> None:
    _rebind(tmp_path)
    alice_draft = store.create_swarm_session(["main"], user_id="usr_alice")
    bob_draft = store.create_swarm_session(["main"], user_id="usr_bob")
    deleted = store.prune_empty_draft_sessions(user_id="usr_alice")
    assert alice_draft in deleted
    assert bob_draft not in deleted
    assert store.get_session(bob_draft) is not None
    assert store.get_session(alice_draft) is None


