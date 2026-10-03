from app.core import self_update


def test_status_identifies_running_service(monkeypatch):
    monkeypatch.setattr(self_update, "can_self_update", lambda **kwargs: (True, "ok"))
    monkeypatch.setattr(self_update, "install_kind", lambda **kwargs: "script")
    monkeypatch.setattr(self_update, "package_version", lambda: "0.3.3")
    monkeypatch.setattr(self_update, "_git_head", lambda **kwargs: "old-head")
    monkeypatch.setattr(self_update, "_spawned_at", None)

    before = self_update.status()
    monkeypatch.setattr(self_update, "_git_head", lambda **kwargs: "new-head")
    after_pull = self_update.status()
    assert before["instance_id"] == after_pull["instance_id"]
    assert before["head"] != after_pull["head"]

    monkeypatch.setattr(self_update, "_INSTANCE_ID", "restarted-service")
    assert self_update.status()["instance_id"] != before["instance_id"]
