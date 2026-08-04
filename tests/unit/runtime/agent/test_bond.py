"""Bond score pure function."""

from __future__ import annotations

from app.runtime.agent.learning.bond import compute_bond


def test_bond_zero() -> None:
    assert compute_bond() == 0


def test_bond_clamps() -> None:
    b = compute_bond(
        chats=10_000,
        saved_events=10_000,
        user_memory_chars=100_000,
        library_skills=10_000,
        days_active=10_000,
    )
    assert 0 <= b <= 100
    assert b == 100


def test_bond_increases_with_saves() -> None:
    a = compute_bond(saved_events=0)
    b = compute_bond(saved_events=5)
    c = compute_bond(saved_events=50)
    assert a < b <= c
