"""Workplace hint matching and strip helpers."""

from __future__ import annotations

from app.runtime.tools.workplace_ctx import match_workplace, strip_workplace_hint


def test_match_by_name_and_host() -> None:
    wps = [
        {
            "id": "wp1",
            "name": "aio-serv",
            "host": "aio-serv.local",
            "connector_hostname": "aio-serv",
            "kind": "tunnel",
        },
        {
            "id": "wp2",
            "name": "other",
            "host": "10.0.0.2",
            "kind": "tunnel",
        },
    ]
    assert match_workplace(wps, "aio-serv")["id"] == "wp1"
    assert match_workplace(wps, "AIO-SERV")["id"] == "wp1"
    assert match_workplace(wps, "wp2")["id"] == "wp2"
    assert match_workplace(wps, "nope") is None


def test_strip_trailing_host() -> None:
    wps = [
        {
            "id": "wp1",
            "name": "aio-serv",
            "host": "aio-serv",
            "kind": "tunnel",
        }
    ]
    text, hint = strip_workplace_hint("please check disk aio-serv", wps)
    assert hint == "aio-serv"
    assert "aio-serv" not in text
    assert "check disk" in text


def test_strip_on_host() -> None:
    wps = [{"id": "wp1", "name": "edge", "host": "edge", "kind": "tunnel"}]
    text, hint = strip_workplace_hint("df -h on edge", wps)
    assert hint == "edge"
    assert text == "df -h"
