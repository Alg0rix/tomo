import json

import pytest
from pydantic import ValidationError

from app.plugins.catalogs import Entry
from app.plugins.icons import ICONS, validate_icon
from app.plugins.manager import PluginManager


def test_named_lucide_icons_and_manifest(tmp_path):
    manifest = {"id": "ledger", "name": "Ledger", "sdk_version": 1, "icon": "wallet"}
    (tmp_path / "tomo-plugin.json").write_text(json.dumps(manifest))
    assert PluginManager.manifest(tmp_path)["icon"] == "wallet"
    assert "<path" in ICONS["wallet"]
    manifest.pop("icon")
    (tmp_path / "tomo-plugin.json").write_text(json.dumps(manifest))
    assert PluginManager.manifest(tmp_path)["icon"] == "puzzle"


@pytest.mark.parametrize(
    "value", ["<svg onload=alert(1)>", "../secret", "unknown", None, 7]
)
def test_invalid_icons_rejected_in_manifests_and_catalogs(tmp_path, value):
    manifest = {"id": "ledger", "name": "Ledger", "sdk_version": 1, "icon": value}
    (tmp_path / "tomo-plugin.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        PluginManager.manifest(tmp_path)
    with pytest.raises(ValidationError):
        Entry.model_validate(
            {
                **manifest,
                "description": "Ledger",
                "version": "0.1",
                "author": "Tomo",
                "source": {"url": "https://github.com/Alg0rix/tomo-plugins"},
            }
        )


def test_registry_is_local_svg_paths_only():
    for name, paths in ICONS.items():
        assert validate_icon(name) == name
        assert "script" not in paths and "onload" not in paths and "http" not in paths
