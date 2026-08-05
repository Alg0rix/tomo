"""Whitespace collapse for HTML→Markdown conversion."""


from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from app.runtime.html_md.dom import ELEMENT_NODE, TEXT_NODE, DomNode

_WS_RE = re.compile(r"[ \r\n\t]+")


def collapse_whitespace(
    *,
    element: DomNode,
    is_block: Callable[[Any], bool],
    is_void: Callable[[Any], bool],
    is_pre: Callable[[Any], bool] | None = None,
) -> None:
    """Remove extraneous whitespace from ``element`` (mutates the tree)."""

    def _is_pre(node: DomNode) -> bool:
        if is_pre is not None:
            return bool(is_pre(node))
        return node.node_name == "PRE"

    if element.first_child is None or _is_pre(element):
        return

    prev_text: DomNode | None = None
    keep_leading_ws = False

    prev: DomNode | None = None
    node: DomNode | None = _next(prev, element, _is_pre)

    while node is not None and node != element:
        if node.node_type in (TEXT_NODE, 4):
            text = _WS_RE.sub(" ", node.data)

            if (
                (prev_text is None or prev_text.data.endswith(" "))
                and not keep_leading_ws
                and text[:1] == " "
            ):
                text = text[1:]

            if not text:
                node = _remove(node)
                continue

            node.data = text
            prev_text = node
        elif node.node_type == ELEMENT_NODE:
            if is_block(node) or node.node_name == "BR":
                if prev_text is not None:
                    prev_text.data = prev_text.data.rstrip(" ")
                prev_text = None
                keep_leading_ws = False
            elif is_void(node) or _is_pre(node):
                prev_text = None
                keep_leading_ws = True
            elif prev_text is not None:
                keep_leading_ws = False
        else:
            node = _remove(node)
            continue

        next_node = _next(prev, node, _is_pre)
        prev = node
        node = next_node

    if prev_text is not None:
        prev_text.data = prev_text.data.rstrip(" ")
        if not prev_text.data:
            _remove(prev_text)


def _remove(node: DomNode) -> DomNode | None:
    nxt = node.next_sibling or node.parent_node
    parent = node.parent_node
    if parent is not None:
        parent.remove_child(node)
    return nxt


def _next(
    prev: DomNode | None,
    current: DomNode,
    is_pre: Callable[[DomNode], bool],
) -> DomNode | None:
    if (prev is not None and prev.parent_node == current) or is_pre(current):
        return current.next_sibling or current.parent_node
    return current.first_child or current.next_sibling or current.parent_node
