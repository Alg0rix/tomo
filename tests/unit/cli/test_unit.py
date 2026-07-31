from pathlib import Path

from cli.unit import parse_tomo_paths_from_unit, render_user_unit


def test_render_includes_required_env() -> None:
    text = render_user_unit()
    assert "Environment=TOMO_HOME=%h/.tomo" in text
    assert "Environment=TOMO_WORK=%h/tomo" in text
    assert "WorkingDirectory=%h/.local/share/tomo/app" in text
    assert "ExecStart=%h/.local/share/tomo/app/.venv/bin/python -m app.main" in text
    assert "WantedBy=default.target" in text


def test_parse_paths_from_unit(tmp_path: Path) -> None:
    text = render_user_unit()
    home_p, work_p = parse_tomo_paths_from_unit(text, tmp_path)
    assert home_p == tmp_path / ".tomo"
    assert work_p == tmp_path / "tomo"


def test_parse_falls_back_when_env_missing(tmp_path: Path) -> None:
    home_p, work_p = parse_tomo_paths_from_unit("[Service]\n", tmp_path)
    assert home_p == tmp_path / ".tomo"
    assert work_p == tmp_path / "tomo"
