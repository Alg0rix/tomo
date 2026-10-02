"""Provider setup → persisted chat selection → real runtime wire routing."""

import json

import httpx2
import pytest
from fastapi.testclient import TestClient

from app.core.deps import require_auth
from app.main import app
from app.runtime.agent.loop import run_turn
from app.runtime.llm import get_auxiliary_llm, get_llm
from app.services import store


def sse(*events):
    return httpx2.Response(
        200,
        headers={"content-type": "text/event-stream"},
        text="".join("data: " + json.dumps(e) + "\n\n" for e in events),
    )


@pytest.mark.asyncio
async def test_deepseek_session_effort_reaches_runtime(tmp_path, monkeypatch):
    store.rebind(tmp_path / "deepseek.db")
    app.dependency_overrides[require_auth] = lambda: None
    wire = []

    def provider(request):
        wire.append(json.loads(request.content))
        return sse({"choices": [{"delta": {"content": "Hello"}, "finish_reason": "stop"}]})

    real_client = httpx2.AsyncClient
    monkeypatch.setattr(
        httpx2, "AsyncClient",
        lambda *a, **kw: real_client(*a, **{**kw, "transport": httpx2.MockTransport(provider)}),
    )
    profile = store.create_llm_profile({
        "name": "Go", "base_url": "https://opencode.ai/zen/go/v1",
        "api_key": "go-token", "model": "deepseek-v4.1-flash",
        "available_models": ["deepseek-v4.1-flash", "deepseek-v4-pro", "kimi-k2.6"],
    })
    store.set_default_llm_profile(profile["id"])
    sid = store.create_swarm_session(["main"])
    api = TestClient(app)
    try:
        state = api.get(f"/api/sessions/{sid}/reasoning-effort")
        assert state.json()["reasoning_efforts"] == ["low", "high", "max"]
        for effort in ("low", "high", "max"):
            selected = api.put(f"/api/sessions/{sid}/reasoning-effort", json={"reasoning_effort": effort})
            assert selected.status_code == 200
            events = [e async for e in run_turn(
                "hello", agent_id="main", session_id=sid, tools=[], system_prompt="Be helpful.",
            )]
            assert not [e for e in events if e.get("kind") == "error"]
            assert wire[-1]["model"] == "deepseek-v4.1-flash"
            assert wire[-1]["reasoning_effort"] == effort
        assert api.put(
            f"/api/sessions/{sid}/reasoning-effort", json={"reasoning_effort": "medium"},
        ).status_code == 400
        pro = api.put(f"/api/sessions/{sid}/model", json={"profile_id": profile["id"], "model": "deepseek-v4-pro"})
        assert pro.json()["reasoning_efforts"] == ["high", "max"]
        kimi = api.put(f"/api/sessions/{sid}/model", json={"profile_id": profile["id"], "model": "kimi-k2.6"})
        assert kimi.json()["reasoning_efforts"] == []
    finally:
        api.close()
        app.dependency_overrides.pop(require_auth, None)


@pytest.mark.asyncio
async def test_connect_select_and_follow_main(tmp_path, monkeypatch):
    store.rebind(tmp_path / "provider.db")
    app.dependency_overrides[require_auth] = lambda: None
    wire = []
    models = ["minimax-m2.7", "kimi-k2.5", "gpt-5.4", "gemini-3-flash"]

    def provider(request):
        if request.headers.get("authorization") == "Bearer bad-token":
            return httpx2.Response(401)
        if request.method == "GET":
            return httpx2.Response(200, json={"data": [{"id": m} for m in models]})
        body = json.loads(request.content)
        wire.append((request.url.path, body))
        assert request.headers.get("x-opencode-session")
        assert request.headers.get("user-agent", "").startswith("tomo/")
        if request.url.path.endswith("/messages"):
            return sse(
                {"type": "message_start", "message": {"usage": {"input_tokens": 5}}},
                {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "text_delta", "text": "Helpful Chat"},
                },
                {"type": "message_delta", "usage": {"output_tokens": 3}},
                {"type": "message_stop"},
            )
        if request.url.path.endswith("/responses"):
            return sse(
                {"type": "response.output_text.delta", "delta": "Helpful Chat"},
                {
                    "type": "response.completed",
                    "response": {"usage": {"input_tokens": 5, "output_tokens": 3}},
                },
            )
        if ":streamGenerateContent" in request.url.path:
            return sse(
                {
                    "candidates": [
                        {
                            "content": {"parts": [{"text": "Helpful Chat"}]},
                            "finishReason": "STOP",
                        }
                    ],
                    "usageMetadata": {"promptTokenCount": 5, "candidatesTokenCount": 3},
                }
            )
        return sse(
            {
                "choices": [
                    {"delta": {"content": "Helpful Chat"}, "finish_reason": "stop"}
                ],
                "usage": {"prompt_tokens": 5, "completion_tokens": 3},
            }
        )

    real_client = httpx2.AsyncClient
    monkeypatch.setattr(
        httpx2,
        "AsyncClient",
        lambda *a, **kw: real_client(
            *a, **{**kw, "transport": httpx2.MockTransport(provider)}
        ),
    )
    api = TestClient(app)
    try:
        failed = api.post(
            "/api/llm-profiles",
            json={
                "name": "Bad",
                "base_url": "https://opencode.ai/zen/go/v1",
                "api_key": "bad-token",
            },
        )
        assert failed.status_code == 400
        assert not store.list_llm_profiles()
        created = api.post(
            "/api/llm-profiles",
            json={
                "name": "Go",
                "base_url": "https://opencode.ai/zen/go/v1",
                "api_key": "go-token",
            },
        )
        assert created.status_code == 200
        go = created.json()
        assert go["available_models"] == models
        assert "go-token" not in created.text
        zen = api.post(
            "/api/llm-profiles",
            json={
                "name": "Zen",
                "base_url": "https://opencode.ai/zen/v1",
                "api_key": "zen-token",
            },
        ).json()
        assert store.get_default_llm_profile_id() == go["id"], "a second profile keeps the main model"
        rejected = api.post(f"/api/llm-profiles/{go['id']}/default", json={"model": "not-listed"})
        assert rejected.status_code == 400
        main = api.post(f"/api/llm-profiles/{go['id']}/default", json={"model": "gpt-5.4"})
        assert main.status_code == 200
        assert store.resolve_llm_profile(None)["model"] == "gpt-5.4"
        sid = store.create_swarm_session(["main"])
        for pid, model, suffix in [
            (go["id"], "minimax-m2.7", "/messages"),
            (go["id"], "kimi-k2.5", "/chat/completions"),
            (go["id"], "gpt-5.4", "/responses"),
            (zen["id"], "gemini-3-flash", ":streamGenerateContent"),
        ]:
            selected = api.put(
                f"/api/sessions/{sid}/model", json={"profile_id": pid, "model": model}
            )
            assert selected.status_code == 200
            assert selected.json()["main_model"] == "gpt-5.4"
            assert (
                api.get(f"/api/sessions/{sid}/reasoning-effort").json()["model"]
                == model
            )
            client = get_llm("main", session_id=sid)
            try:
                response = await client.complete([{"role": "user", "content": "hello"}])
                assert response.content == "Helpful Chat"
                assert wire[-1][0].endswith(suffix)
            finally:
                await client.aclose()
        # The production loop must also honor the session, not only the factory.
        store.set_session_model(sid, go["id"], "minimax-m2.7")
        events = [
            e
            async for e in run_turn(
                "hello",
                agent_id="main",
                session_id=sid,
                tools=[],
                system_prompt="Be helpful.",
            )
        ]
        assert not [e for e in events if e.get("kind") == "error"]
        assert wire[-1][0].endswith("/messages")
        for task in ("memory_extraction", "session_title"):
            client = get_auxiliary_llm(task, session_id=sid)
            try:
                assert (
                    await client.complete([{"role": "user", "content": "title"}])
                ).content
                assert wire[-1][1]["model"] == "minimax-m2.7"
            finally:
                await client.aclose()
        store.update_settings(
            {
                "session_title_profile_id": go["id"],
                "session_title_model_name": "gpt-5.4",
            }
        )
        client = get_auxiliary_llm("session_title", session_id=sid)
        try:
            await client.complete([{"role": "user", "content": "title"}])
            assert wire[-1][1]["model"] == "gpt-5.4"
        finally:
            await client.aclose()
        assert (
            api.put(
                f"/api/sessions/{sid}/model",
                json={"profile_id": go["id"], "model": "not-in-catalog"},
            ).status_code
            == 400
        )
        reset = api.put(f"/api/sessions/{sid}/model", json={}).json()
        assert reset["selected_model_profile_id"] == ""
    finally:
        api.close()
        app.dependency_overrides.pop(require_auth, None)
