"""Open-redirect guard for login ``next`` parameter."""

from __future__ import annotations

from app.web.auth import safe_next_path


def test_safe_next_allows_relative_paths() -> None:
    assert safe_next_path("/sessions") == "/sessions"
    assert safe_next_path("/agents?x=1") == "/agents?x=1"
    assert safe_next_path("/") == "/"


def test_safe_next_blocks_external_and_protocol_relative() -> None:
    assert safe_next_path("https://evil.example/phish") == "/"
    assert safe_next_path("//evil.example/phish") == "/"
    assert safe_next_path("https:/evil.example") == "/"
    assert safe_next_path("javascript:alert(1)") == "/"


def test_safe_next_blocks_backslash_tricks() -> None:
    # Backslash-normalized protocol-relative / host forms.
    assert safe_next_path("\\\\evil.example") == "/"
    assert safe_next_path("/\\/evil.example") == "/"  # -> ///evil.example
    assert safe_next_path("//evil.example/phish") == "/"
