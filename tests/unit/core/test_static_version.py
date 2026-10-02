from app.core import deps


def test_static_version_changes_when_an_asset_changes(tmp_path, monkeypatch):
    monkeypatch.setattr(deps, "STATIC_DIR", tmp_path)
    (tmp_path / "app.js").write_text("one")
    ver = deps._StaticVersion()
    first = str(ver)
    assert str(ver) == first

    (tmp_path / "app.js").write_text("two!")
    ver._checked = 0.0  # skip the TTL
    assert str(ver) != first
