"""Shared helpers for the HTML→Markdown converter."""

from __future__ import annotations

import re
from typing import Any

BLOCK_ELEMENTS = frozenset(
    {
        "ADDRESS",
        "ARTICLE",
        "ASIDE",
        "AUDIO",
        "BLOCKQUOTE",
        "BODY",
        "CANVAS",
        "CENTER",
        "DD",
        "DIR",
        "DIV",
        "DL",
        "DT",
        "FIELDSET",
        "FIGCAPTION",
        "FIGURE",
        "FOOTER",
        "FORM",
        "FRAMESET",
        "H1",
        "H2",
        "H3",
        "H4",
        "H5",
        "H6",
        "HEADER",
        "HGROUP",
        "HR",
        "HTML",
        "ISINDEX",
        "LI",
        "MAIN",
        "MENU",
        "NAV",
        "NOFRAMES",
        "NOSCRIPT",
        "OL",
        "OUTPUT",
        "P",
        "PRE",
        "SECTION",
        "TABLE",
        "TBODY",
        "TD",
        "TFOOT",
        "TH",
        "THEAD",
        "TR",
        "UL",
    }
)

VOID_ELEMENTS = frozenset(
    {
        "AREA",
        "BASE",
        "BR",
        "COL",
        "COMMAND",
        "EMBED",
        "HR",
        "IMG",
        "INPUT",
        "KEYGEN",
        "LINK",
        "META",
        "PARAM",
        "SOURCE",
        "TRACK",
        "WBR",
    }
)

MEANINGFUL_WHEN_BLANK = frozenset(
    {
        "A",
        "TABLE",
        "THEAD",
        "TBODY",
        "TFOOT",
        "TH",
        "TD",
        "IFRAME",
        "SCRIPT",
        "AUDIO",
        "VIDEO",
    }
)

_MARKDOWN_ESCAPES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\\"), r"\\\\"),
    (re.compile(r"\*"), r"\*"),
    (re.compile(r"^-"), r"\-"),
    (re.compile(r"^\+ "), r"\+ "),
    (re.compile(r"^(=+)"), r"\\\1"),
    (re.compile(r"^(#{1,6}) "), r"\\\1 "),
    (re.compile(r"`"), r"\`"),
    (re.compile(r"^~~~"), r"\~~~"),
    (re.compile(r"\["), r"\["),
    (re.compile(r"\]"), r"\]"),
    (re.compile(r"^>"), r"\>"),
    (re.compile(r"_"), r"\_"),
    (re.compile(r"^(\d+)\. "), r"\1\\. "),
]


def extend(destination: dict[str, Any], *sources: dict[str, Any]) -> dict[str, Any]:
    for source in sources:
        for key, value in source.items():
            destination[key] = value
    return destination


def repeat(character: str, count: int) -> str:
    return character * count


def trim_leading_newlines(string: str) -> str:
    return string.lstrip("\n")


def trim_trailing_newlines(string: str) -> str:
    # Avoid match-at-end regexp bottleneck.
    index_end = len(string)
    while index_end > 0 and string[index_end - 1] == "\n":
        index_end -= 1
    return string[:index_end]


def trim_newlines(string: str) -> str:
    return trim_trailing_newlines(trim_leading_newlines(string))


def is_block(node: Any) -> bool:
    return getattr(node, "node_name", "") in BLOCK_ELEMENTS


def is_void(node: Any) -> bool:
    return getattr(node, "node_name", "") in VOID_ELEMENTS


def has_void(node: Any) -> bool:
    return any(el.node_name in VOID_ELEMENTS for el in node.get_elements_by_tag_name("*"))


def is_meaningful_when_blank(node: Any) -> bool:
    return getattr(node, "node_name", "") in MEANINGFUL_WHEN_BLANK


def has_meaningful_when_blank(node: Any) -> bool:
    return any(
        el.node_name in MEANINGFUL_WHEN_BLANK
        for el in node.get_elements_by_tag_name("*")
    )


def escape_markdown(string: str) -> str:
    out = string
    for pattern, repl in _MARKDOWN_ESCAPES:
        out = pattern.sub(repl, out)
    return out
