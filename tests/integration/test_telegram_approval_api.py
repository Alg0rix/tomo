"""Web approval controls cannot bypass session ownership or offered choices."""

from fastapi.testclient import TestClient

from app.core.deps import require_auth
from app.main import app
from app.runtime.permissions import hitl
from app.services import store


def test_approval_and_clarify_web_resolvers_check_owner(tmp_path):
    store.rebind(tmp_path / "approval-owner.db")
    app.dependency_overrides.pop(require_auth, None)
    hitl.clear_all_pending()
    alice = store.create_user(
        {"username": "owner", "password": "password1", "role": "member"}
    )
    store.create_user(
        {"username": "stranger", "password": "password1", "role": "member"}
    )
    sid = store.create_swarm_session(["main"], user_id=alice["id"])
    approval = hitl.create_approval(
        tool="bash",
        args={},
        findings=[],
        description="test",
        session_id=sid,
        allow_permanent=False,
    )
    clarify = hitl.create_clarify(question="Which environment?", session_id=sid)
    try:
        with TestClient(app) as client:
            client.post(
                "/login", data={"username": "stranger", "password": "password1"}
            )
            assert (
                client.post(
                    "/api/approvals/" + approval["id"], json={"choice": "once"}
                ).status_code
                == 404
            )
            assert (
                client.post(
                    "/api/clarify/" + clarify["id"], json={"answer": "Prod"}
                ).status_code
                == 404
            )
            assert len(hitl.list_pending_for_session(sid)["approvals"]) == 1
            client.post("/login", data={"username": "owner", "password": "password1"})
            assert (
                client.post(
                    "/api/approvals/" + approval["id"], json={"choice": "always"}
                ).status_code
                == 400
            )
            assert (
                client.post(
                    "/api/approvals/" + approval["id"], json={"choice": "once"}
                ).status_code
                == 200
            )
            assert (
                client.post(
                    "/api/approvals/" + approval["id"], json={"choice": "once"}
                ).status_code
                == 409
            )
            assert (
                client.post(
                    "/api/clarify/" + clarify["id"], json={"answer": "Dev"}
                ).status_code
                == 200
            )
    finally:
        hitl.clear_all_pending()
