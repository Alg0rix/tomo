"""Node decoration for the HTML→Markdown converter."""

from __future__ import annotations

import re
from typing import Any

from app.runtime.html_md.dom import DomNode
from app.runtime.html_md.utilities import (
    has_meaningful_when_blank,
    has_void,
    is_block,
    is_meaningful_when_blank,
    is_void,
)

_EDGE_WS = re.compile(
    r"^(([ \t\r\n]*)(\s*))(?:(?=\S)[\s\S]*\S)?((\s*?)([ \t\r\n]*))$"
)


def decorate_node(node: DomNode, options: dict[str, Any]) -> DomNode:
    """Annotate ``node`` with isBlock / isCode / isBlank / flankingWhitespace."""
    parent = node.parent_node
    node.is_block = is_block(node)
    node.is_code = node.node_name == "CODE" or bool(parent and parent.is_code)
    node.is_blank = _is_blank(node)
    node.flanking_whitespace = _flanking_whitespace(node, options)
    return node


def _is_blank(node: DomNode) -> bool:
    return (
        not is_void(node)
        and not is_meaningful_when_blank(node)
        and bool(re.match(r"^\s*$", node.text_content or ""))
        and not has_void(node)
        and not has_meaningful_when_blank(node)
    )


def _flanking_whitespace(node: DomNode, options: dict[str, Any]) -> dict[str, str]:
    if node.is_block or (options.get("preformatted_code") and node.is_code):
        return {"leading": "", "trailing": ""}

    edges = _edge_whitespace(node.text_content or "")

    if edges["leading_ascii"] and _is_flanked_by_whitespace("left", node, options):
        edges["leading"] = edges["leading_non_ascii"]

    if edges["trailing_ascii"] and _is_flanked_by_whitespace("right", node, options):
        edges["trailing"] = edges["trailing_non_ascii"]

    return {"leading": edges["leading"], "trailing": edges["trailing"]}


def _edge_whitespace(string: str) -> dict[str, str]:
    m = _EDGE_WS.match(string)
    if not m:
        return {
            "leading": "",
            "leading_ascii": "",
            "leading_non_ascii": "",
            "trailing": "",
            "trailing_non_ascii": "",
            "trailing_ascii": "",
        }
    return {
        "leading": m.group(1) or "",
        "leading_ascii": m.group(2) or "",
        "leading_non_ascii": m.group(3) or "",
        "trailing": m.group(4) or "",
        "trailing_non_ascii": m.group(5) or "",
        "trailing_ascii": m.group(6) or "",
    }


def _is_flanked_by_whitespace(
    side: str, node: DomNode, options: dict[str, Any]
) -> bool:
    if side == "left":
        sibling = node.previous_sibling
        pattern = re.compile(r" $")
    else:
        sibling = node.next_sibling
        pattern = re.compile(r"^ ")

    if sibling is None:
        return False

    if sibling.node_type == 3:
        return bool(pattern.search(sibling.node_value))
    if options.get("preformatted_code") and sibling.node_name == "CODE":
        return False
    if sibling.node_type == 1 and not is_block(sibling):
        return bool(pattern.search(sibling.text_content or ""))
    return False
