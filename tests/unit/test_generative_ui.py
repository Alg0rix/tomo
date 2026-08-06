import json

import pytest

from app.channels.sse_map import map_loop_event
from app.runtime.tools.render_ui import run
from app.runtime.ui import UIValidationError, validate_ui_payload


def _payload():
    return {
        "ui_id": "checkout-1",
        "tree": {
            "type": "card",
            "title": "Checkout",
            "children": [
                {"type": "text", "value": "Total: Rp150.000"},
                {"type": "button", "id": "pay", "label": "Pay", "action": "pay"},
            ],
        },
    }


def test_render_ui_returns_normalized_json():
    result = json.loads(run(_payload()))
    assert result["ui_id"] == "checkout-1"
    assert result["mode"] == "replace"
    assert result["tree"]["children"][1]["action"] == "pay"


def test_render_ui_rejects_unsafe_or_unknown_nodes():
    with pytest.raises(UIValidationError):
        validate_ui_payload({"ui_id": "x", "tree": {"type": "html", "value": "<script>"}})


def test_render_ui_rejects_deep_trees():
    tree = {"type": "stack", "children": []}
    current = tree
    for _ in range(9):
        child = {"type": "stack", "children": []}
        current["children"].append(child)
        current = child
    with pytest.raises(UIValidationError):
        validate_ui_payload({"ui_id": "deep", "tree": tree})


def test_ui_event_is_sse_and_persistable_history_entry():
    chunks, entries, seq = map_loop_event(
        {
            "kind": "ui",
            "ui_id": "chart-1",
            "mode": "replace",
            "tree": {"type": "text", "value": "Ready"},
        },
        "agent",
        "Tomo",
        3,
        "turn_1",
    )
    assert seq == 4
    assert "event: ui" in chunks[0]
    assert entries[0]["type"] == "ui"
    assert entries[0]["params"]["ui_id"] == "chart-1"
