"""HtmlToMarkdown — HTML to CommonMark Markdown."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from app.runtime.html_md.commonmark_rules import build_commonmark_rules
from app.runtime.html_md.dom import DOCUMENT_FRAGMENT_NODE, DOCUMENT_NODE, DomNode
from app.runtime.html_md.node import decorate_node
from app.runtime.html_md.root_node import root_node
from app.runtime.html_md.rules import Filter, Rules
from app.runtime.html_md.utilities import (
    escape_markdown,
    extend,
    trim_leading_newlines,
    trim_trailing_newlines,
)

Plugin = Callable[["HtmlToMarkdown"], None]


def _blank_replacement(content: str, node: DomNode, options: Any = None) -> str:
    return "\n\n" if node.is_block else ""


def _keep_replacement(content: str, node: DomNode, options: Any = None) -> str:
    html = node.outer_html
    return f"\n\n{html}\n\n" if node.is_block else html


def _default_replacement(content: str, node: DomNode, options: Any = None) -> str:
    return f"\n\n{content}\n\n" if node.is_block else content


class HtmlToMarkdown:
    """Convert HTML to Markdown (CommonMark)."""

    def __init__(self, options: dict[str, Any] | None = None) -> None:
        defaults: dict[str, Any] = {
            "rules": build_commonmark_rules(),
            "heading_style": "setext",
            "hr": "* * *",
            "bullet_list_marker": "*",
            "code_block_style": "indented",
            "fence": "```",
            "em_delimiter": "_",
            "strong_delimiter": "**",
            "link_style": "inlined",
            "link_reference_style": "full",
            "br": "  ",
            "preformatted_code": False,
            "blank_replacement": _blank_replacement,
            "keep_replacement": _keep_replacement,
            "default_replacement": _default_replacement,
        }
        # Accept both snake_case (Python) and camelCase (JS) option keys.
        normalized = _normalize_options(options or {})
        self.options = extend({}, defaults, normalized)
        if "rules" not in normalized:
            self.options["rules"] = build_commonmark_rules()
        self.rules = Rules(self.options)

    def convert(self, input_html: str | DomNode) -> str:
        if not _can_convert(input_html):
            raise TypeError(
                f"{input_html!r} is not a string, or an element/document/fragment node."
            )
        if input_html == "":
            return ""
        output = self._process(root_node(input_html, self.options))
        return self._post_process(output)

    def use(self, plugin: Plugin | Sequence[Plugin]) -> HtmlToMarkdown:
        if isinstance(plugin, (list, tuple)):
            for p in plugin:
                self.use(p)
        elif callable(plugin):
            plugin(self)
        else:
            raise TypeError("plugin must be a Function or an Array of Functions")
        return self

    def add_rule(self, key: str, rule: dict[str, Any]) -> HtmlToMarkdown:
        self.rules.add(key, rule)
        return self

    def keep(self, filter: Filter) -> HtmlToMarkdown:
        self.rules.keep(filter)
        return self

    def remove(self, filter: Filter) -> HtmlToMarkdown:
        self.rules.remove(filter)
        return self

    def escape(self, string: str) -> str:
        return escape_markdown(string)

    # ── internals ────────────────────────────────────────────────────

    def _process(self, parent_node: DomNode) -> str:
        output = ""
        for child in parent_node.child_nodes:
            node = decorate_node(child, self.options)
            replacement = ""
            if node.node_type == 3:
                replacement = (
                    node.node_value if node.is_code else self.escape(node.node_value)
                )
            elif node.node_type == 1:
                replacement = self._replacement_for_node(node)
            output = _join(output, replacement)
        return output

    def _replacement_for_node(self, node: DomNode) -> str:
        rule = self.rules.for_node(node)
        content = self._process(node)
        whitespace = node.flanking_whitespace
        if whitespace["leading"] or whitespace["trailing"]:
            content = content.strip()
        return (
            whitespace["leading"]
            + rule["replacement"](content, node, self.options)
            + whitespace["trailing"]
        )

    def _post_process(self, output: str) -> str:
        def append_rule(rule: dict[str, Any], _i: int) -> None:
            nonlocal output
            append = rule.get("append")
            if callable(append):
                output = _join(output, append(self.options))

        self.rules.for_each(append_rule)
        return output.lstrip("\t\r\n").rstrip("\t\r\n ")


def _join(output: str, replacement: str) -> str:
    s1 = trim_trailing_newlines(output)
    s2 = trim_leading_newlines(replacement)
    nls = max(len(output) - len(s1), len(replacement) - len(s2))
    separator = "\n\n"[:nls]
    return s1 + separator + s2


def _can_convert(input_html: Any) -> bool:
    if input_html is None:
        return False
    if isinstance(input_html, str):
        return True
    if isinstance(input_html, DomNode):
        return input_html.node_type in {
            1,
            DOCUMENT_NODE,
            DOCUMENT_FRAGMENT_NODE,
        }
    return False


_CAMEL_TO_SNAKE = {
    "headingStyle": "heading_style",
    "bulletListMarker": "bullet_list_marker",
    "codeBlockStyle": "code_block_style",
    "emDelimiter": "em_delimiter",
    "strongDelimiter": "strong_delimiter",
    "linkStyle": "link_style",
    "linkReferenceStyle": "link_reference_style",
    "preformattedCode": "preformatted_code",
    "blankReplacement": "blank_replacement",
    "keepReplacement": "keep_replacement",
    "defaultReplacement": "default_replacement",
}


def _normalize_options(options: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in options.items():
        out[_CAMEL_TO_SNAKE.get(key, key)] = value
    return out
