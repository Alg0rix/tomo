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


def test_find_uv_falls_back_to_local_bin(tmp_path: Path) -> None:
    uv_bin = tmp_path / ".local" / "bin" / "uv"
    uv_bin.parent.mkdir(parents=True)
    uv_bin.write_text("#!/bin/sh\n", encoding="utf-8")
    with patch("cli.update_cmd.shutil.which", return_value=None):
        assert _find_uv(tmp_path) == str(uv_bin)


def test_find_uv_missing(tmp_path: Path) -> None:
    with patch("cli.update_cmd.shutil.which", return_value=None):
        assert _find_uv(tmp_path) is None
