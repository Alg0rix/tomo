"""Rule collection for the HTML→Markdown converter."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

Filter = str | list[str] | Callable[..., bool]
Rule = dict[str, Any]


class Rules:
    def __init__(self, options: dict[str, Any]) -> None:
        self.options = options
        self._keep: list[Rule] = []
        self._remove: list[Rule] = []
        self.blank_rule: Rule = {"replacement": options["blank_replacement"]}
        self.keep_replacement = options["keep_replacement"]
        self.default_rule: Rule = {"replacement": options["default_replacement"]}
        self.array: list[Rule] = list(options["rules"].values())

    def add(self, key: str, rule: Rule) -> None:
        self.array.insert(0, rule)

    def keep(self, filter: Filter) -> None:
        self._keep.insert(0, {"filter": filter, "replacement": self.keep_replacement})

    def remove(self, filter: Filter) -> None:
        self._remove.insert(
            0, {"filter": filter, "replacement": lambda content, node, options: ""}
        )

    def for_node(self, node: Any) -> Rule:
        if getattr(node, "is_blank", False):
            return self.blank_rule
        rule = _find_rule(self.array, node, self.options)
        if rule is not None:
            return rule
        rule = _find_rule(self._keep, node, self.options)
        if rule is not None:
            return rule
        rule = _find_rule(self._remove, node, self.options)
        if rule is not None:
            return rule
        return self.default_rule

    def for_each(self, fn: Callable[[Rule, int], None]) -> None:
        for i, rule in enumerate(self.array):
            fn(rule, i)


def _find_rule(rules: list[Rule], node: Any, options: dict[str, Any]) -> Rule | None:
    for rule in rules:
        if _filter_value(rule, node, options):
            return rule
    return None


def _filter_value(rule: Rule, node: Any, options: dict[str, Any]) -> bool:
    filt = rule.get("filter")
    name = getattr(node, "node_name", "").lower()
    if isinstance(filt, str):
        return filt == name
    if isinstance(filt, list):
        return name in filt
    if callable(filt):
        return bool(filt(node, options))
    raise TypeError("`filter` needs to be a string, array, or function")
