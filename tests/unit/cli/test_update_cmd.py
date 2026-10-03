from pathlib import Path
from unittest.mock import patch

from cli.git_sync import GitSyncResult
from cli.update_cmd import _find_uv, cmd_update


def test_update_missing_install(tmp_path: Path) -> None:
    code = cmd_update(assume_yes=True, home=tmp_path)
    assert code != 0


def test_update_runs_sync_and_restart(tmp_path: Path) -> None:
    app = tmp_path / ".local/share/tomo/app"
    app.mkdir(parents=True)
    (app / ".git").mkdir()
    with (
        patch("cli.update_cmd.sync_to_origin") as sync,
        patch("cli.update_cmd.systemctl_user") as sc,
        patch("cli.update_cmd._uv_sync", return_value=0) as uv,
    ):
        sync.return_value = GitSyncResult(
            updated=True,
            commits=2,
            head="abc1234",
            stash_ref=None,
            used_hard_reset=False,
        )
        sc.return_value = type("P", (), {"returncode": 0, "stdout": "", "stderr": ""})()
        code = cmd_update(assume_yes=True, home=tmp_path)
    assert code == 0
    sync.assert_called_once()
    uv.assert_called_once()
    sc.assert_called()


def test_find_uv_missing(tmp_path: Path) -> None:
    with patch("cli.update_cmd.shutil.which", return_value=None):
        assert _find_uv(tmp_path) is None


def test_update_restores_overlay_with_new_core_interpreter(tmp_path, monkeypatch):
    from cli.update_cmd import _restore_plugin_dependencies
    import subprocess

    app = tmp_path / ".local/share/tomo/app"
    state = tmp_path / ".tomo/plugins/dependencies/active.json"
    state.parent.mkdir(parents=True)
    state.write_text("{}")
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr("cli.update_cmd.subprocess.run", run)
    assert _restore_plugin_dependencies(app, tmp_path) == 0
    assert calls[0][0] == [
        str(app / ".venv/bin/python"),
        "-m",
        "app.plugins.dependencies",
        str(tmp_path / ".tomo"),
    ]
