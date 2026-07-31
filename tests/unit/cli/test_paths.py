from pathlib import Path

from cli.paths import (
    cli_symlink_path,
    default_tomo_home,
    default_tomo_work,
    install_dir,
    read_tracked_branch,
    unit_path,
    write_tracked_branch,
)


def test_paths_under_fake_home(tmp_path: Path) -> None:
    assert install_dir(tmp_path) == tmp_path / ".local/share/tomo/app"
    assert unit_path(tmp_path) == tmp_path / ".config/systemd/user/tomo.service"
    assert cli_symlink_path(tmp_path) == tmp_path / ".local/bin/tomo"
    assert default_tomo_home(tmp_path) == tmp_path / ".tomo"
    assert default_tomo_work(tmp_path) == tmp_path / "tomo"


def test_tracked_branch_roundtrip(tmp_path: Path) -> None:
    app = tmp_path / "app"
    app.mkdir()
    assert read_tracked_branch(app) == "main"
    write_tracked_branch(app, "develop")
    assert read_tracked_branch(app) == "develop"
