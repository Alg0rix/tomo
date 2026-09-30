"""Tool schema ordering must not depend on discovery order or dict insertion."""
import copy
import json

from app.runtime.llm.prompt_cache import stable_tools


def test_tool_discovery_order_and_object_order_are_canonicalized():
    first = [{"type": "function", "function": {"name": "z", "parameters": {
        "type": "object", "properties": {"b": {"type": "string"}, "a": {"type": "integer"}},
        "required": ["b", "a"],
    }}}, {"type": "function", "function": {"name": "a"}}]
    original = copy.deepcopy(first)
    second = [{"function": {"name": "a"}, "type": "function"},
              {"function": {"parameters": {"required": ["b", "a"], "properties": {
                  "a": {"type": "integer"}, "b": {"type": "string"}}, "type": "object"},
                  "name": "z"}, "type": "function"}]
    assert json.dumps(stable_tools(first)) == json.dumps(stable_tools(second))
    assert first == original
    z = next(t for t in stable_tools(first) if t["function"]["name"] == "z")
    assert z["function"]["parameters"]["required"] == ["b", "a"]


def test_empty_tools_remain_empty():
    assert stable_tools(None) is None
    assert stable_tools([]) == []
