from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from cli.unit import render_user_unit
from cli.uninstall_cmd import uninstall


def test_uninstall_removes_code_keeps_data(tmp_path: Path) -> None:
    app = tmp_path / ".local/share/tomo/app"
    app.mkdir(parents=True)
    (app / "x").write_text("1", encoding="utf-8")
    unit = tmp_path / ".config/systemd/user/tomo.service"
    unit.parent.mkdir(parents=True)
    unit.write_text(render_user_unit(), encoding="utf-8")
    link = tmp_path / ".local/bin/tomo"
    link.parent.mkdir(parents=True)
    link.symlink_to(app / ".venv/bin/tomo")
    data = tmp_path / ".tomo"
    data.mkdir()
    (data / "keep").write_text("1", encoding="utf-8")
    work = tmp_path / "tomo"
    work.mkdir()

    calls: list[tuple] = []

    def fake_systemctl(*args: str, check: bool = False):
        calls.append(args)
        return MagicMock(returncode=0)

    uninstall(
        purge=False,
        assume_yes=True,
        home=tmp_path,
        run_systemctl=fake_systemctl,
    )

    assert not app.exists()
    assert data.exists()
    assert work.exists()
    assert not unit.exists()
    assert not link.exists()
    assert any("stop" in a for a in calls)


def test_uninstall_purge_deletes_data(tmp_path: Path) -> None:
    app = tmp_path / ".local/share/tomo/app"
    app.mkdir(parents=True)
    unit = tmp_path / ".config/systemd/user/tomo.service"
    unit.parent.mkdir(parents=True)
    unit.write_text(render_user_unit(), encoding="utf-8")
    data = tmp_path / ".tomo"
    data.mkdir()
    work = tmp_path / "tomo"
    work.mkdir()

    uninstall(
        purge=True,
        assume_yes=True,
        home=tmp_path,
        run_systemctl=lambda *a, check=False: MagicMock(returncode=0),
    )
    assert not data.exists()
    assert not work.exists()


def test_uninstall_purge_aborts_without_yes(tmp_path: Path) -> None:
    (tmp_path / ".local/share/tomo/app").mkdir(parents=True)
    unit = tmp_path / ".config/systemd/user/tomo.service"
    unit.parent.mkdir(parents=True)
    unit.write_text(render_user_unit(), encoding="utf-8")
    (tmp_path / ".tomo").mkdir()

    try:
        uninstall(
            purge=True,
            assume_yes=False,
            home=tmp_path,
            input_fn=lambda _: "n",
            run_systemctl=lambda *a, check=False: MagicMock(returncode=0),
        )
    except SystemExit as e:
        assert e.code != 0
    else:
        raise AssertionError("expected SystemExit on aborted purge")
    assert (tmp_path / ".tomo").exists()
