"""Slash /auto /smart /manual tests."""

from __future__ import annotations

from app.runtime.permissions.modes import clear_session_modes, get_effective_mode
from app.runtime.permissions.slash import handle_approval_slash


def setup_function() -> None:
    clear_session_modes()


def teardown_function() -> None:
    clear_session_modes()


def test_auto_toggles() -> None:
    n1 = handle_approval_slash("/auto", "s1")
    assert n1 is not None and "AUTO on" in n1
    assert get_effective_mode("s1") == "off"
    n2 = handle_approval_slash("/auto", "s1")
    assert n2 is not None and "AUTO off" in n2
    assert get_effective_mode("s1") != "off"


def test_manual_sets_mode() -> None:
    assert handle_approval_slash("/manual", "s1") is not None
    assert get_effective_mode("s1") == "manual"


def test_unknown_returns_none() -> None:
    assert handle_approval_slash("/hallmark", "s1") is None
    assert handle_approval_slash("hello", "s1") is None
