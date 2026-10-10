import json

import pytest

from app.channels.sse_map import map_loop_event
from app.runtime.tools.render_ui import run
from app.runtime.ui import UIValidationError, validate_ui_action, validate_ui_payload


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
        validate_ui_payload(
            {"ui_id": "x", "tree": {"type": "html", "value": "<script>"}}
        )


def test_render_ui_rejects_deep_trees():
    tree = {"type": "stack", "children": []}
    current = tree
    for _ in range(9):
        child = {"type": "stack", "children": []}
        current["children"].append(child)
        current = child
    with pytest.raises(UIValidationError):
        validate_ui_payload({"ui_id": "deep", "tree": tree})


def test_render_ui_v2_patch_and_state_are_normalized():
    payload = validate_ui_payload(
        {
            "ui_id": "checkout-1",
            "mode": "patch",
            "patch": [
                {"op": "replace", "path": "/tree/children/0/value", "value": "Paid"},
                {"op": "add", "path": "/state/order_id", "value": "ord-7"},
            ],
            "state": {"status": "pending"},
        }
    )
    assert payload["mode"] == "patch"
    assert payload["patch"][1]["path"] == "/state/order_id"
    assert payload["state"] == {"status": "pending"}

    with pytest.raises(UIValidationError):
        validate_ui_payload(
            {
                "ui_id": "checkout-1",
                "mode": "patch",
                "patch": [{"op": "replace", "path": "/document/title", "value": "x"}],
            }
        )


def test_ui_action_is_typed_and_bounded():
    action = validate_ui_action(
        {"ui_id": "checkout-1", "action": "pay.now", "payload": {"amount": "10"}}
    )
    assert action == {
        "ui_id": "checkout-1",
        "action": "pay.now",
        "payload": {"amount": "10"},
    }
    with pytest.raises(UIValidationError):
        validate_ui_action({"ui_id": "checkout-1", "action": "bad action"})


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


def test_ui_patch_event_keeps_patch_and_state_on_wire_and_history():
    chunks, entries, _ = map_loop_event(
        {
            "kind": "ui",
            "ui_id": "chart-1",
            "mode": "patch",
            "patch": [{"op": "replace", "path": "/state/status", "value": "done"}],
            "state": {"status": "running"},
        },
        "agent",
        "Tomo",
        3,
        "turn_1",
    )
    assert '"patch"' in chunks[0]
    assert entries[0]["params"]["patch"][0]["path"] == "/state/status"
    assert entries[0]["params"]["state"] == {"status": "running"}


def test_render_ui_infers_type_for_containers_missing_type():
    """Models often omit type on wrapper nodes that only have children."""
    payload = validate_ui_payload(
        {
            "ui_id": "dash_1",
            "tree": {
                "title": "Penjualan",
                "children": [
                    {
                        "children": [
                            {"type": "text", "value": "Omzet"},
                            {
                                "data": [
                                    {"label": "Jan", "value": 10},
                                    {"label": "Feb", "value": 20},
                                ]
                            },
                        ]
                    }
                ],
            },
        }
    )
    assert payload["tree"]["type"] == "card"
    assert payload["tree"]["children"][0]["type"] == "stack"
    assert payload["tree"]["children"][0]["children"][1]["type"] == "chart"


def test_render_ui_still_rejects_empty_uninferable_node():
    with pytest.raises(UIValidationError, match="node.type must be a string"):
        validate_ui_payload({"ui_id": "x", "tree": {"title": "Nope"}})


def test_sandbox_survives_tool_and_history_transport():
    tree = {
        "type": "sandbox",
        "title": "Bill splitter",
        "html": '<input id="total">',
        "css": "input { width: 100%; }",
        "jsFunctions": "function update() {}",
        "jsExpressions": "update();",
        "initialHeight": 320,
    }
    payload = json.loads(run({"ui_id": "splitter", "tree": tree}))
    assert payload["tree"] == tree
    chunks, entries, _ = map_loop_event(
        {"kind": "ui", **payload}, "agent", "Tomo", 0, "turn_sandbox"
    )
    assert "event: ui" in chunks[0]
    assert entries[0]["params"]["tree"] == tree


@pytest.mark.parametrize(
    "field,value",
    [
        ("html", None),
        ("html", "x" * 20_001),
        ("css", {}),
        ("jsFunctions", []),
        ("jsExpressions", 1),
        ("initialHeight", True),
        ("initialHeight", 119),
        ("initialHeight", 1201),
    ],
)
def test_sandbox_rejects_invalid_content(field, value):
    with pytest.raises(UIValidationError):
        validate_ui_payload(
            {
                "ui_id": "sandbox",
                "tree": {
                    "type": "sandbox",
                    "html": "<p>Hello</p>",
                    field: value,
                },
            }
        )


@pytest.mark.parametrize("key", ["__proto__", "constructor", "prototype"])
def test_ui_patch_rejects_prototype_paths(key):
    with pytest.raises(UIValidationError, match="reserved key"):
        validate_ui_payload(
            {
                "ui_id": "sandbox",
                "mode": "patch",
                "patch": [
                    {"op": "add", "path": f"/state/{key}/polluted", "value": True},
                ],
            }
        )


def test_ui_rejects_compositions_that_would_be_truncated_by_agent_loop():
    with pytest.raises(UIValidationError, match="60000"):
        validate_ui_payload({"ui_id": "large", "tree": {
            "type": "sandbox", "html": "h" * 20_000,
            "css": "c" * 20_000, "jsFunctions": "j" * 20_000,
        }})
