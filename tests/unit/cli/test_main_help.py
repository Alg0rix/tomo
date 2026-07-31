import pytest

from cli.__main__ import build_parser


def test_update_help() -> None:
    with pytest.raises(SystemExit) as ei:
        build_parser().parse_args(["update", "--help"])
    assert ei.value.code == 0


def test_uninstall_help() -> None:
    with pytest.raises(SystemExit) as ei:
        build_parser().parse_args(["uninstall", "--help"])
    assert ei.value.code == 0


def test_service_choices() -> None:
    args = build_parser().parse_args(["service", "status"])
    assert args.cmd == "service"
    assert args.action == "status"
