"""Root node preparation before conversion."""

from __future__ import annotations

from typing import Any

from app.runtime.html_md.collapse_whitespace import collapse_whitespace
from app.runtime.html_md.dom import DomNode, parse_html_fragment
from app.runtime.html_md.utilities import is_block, is_void


def root_node(input_html: str | DomNode, options: dict[str, Any]) -> DomNode:
    if isinstance(input_html, str):
        root = parse_html_fragment(input_html)
    else:
        root = input_html.clone_node(True)

    def is_pre_or_code(node: DomNode) -> bool:
        return node.node_name in {"PRE", "CODE"}

    collapse_whitespace(
        element=root,
        is_block=is_block,
        is_void=is_void,
        is_pre=is_pre_or_code if options.get("preformatted_code") else None,
    )
    return root
