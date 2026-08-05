"""CommonMark conversion rules."""

from __future__ import annotations

import re
from typing import Any

from app.runtime.html_md.utilities import escape_markdown, repeat, trim_newlines

Rule = dict[str, Any]


def _clean_attribute(attribute: str | None) -> str:
    if not attribute:
        return ""
    return re.sub(r"(\n+\s*)+", "\n", attribute)


def _escape_link_destination(destination: str) -> str:
    escaped = re.sub(r"([<>()])", r"\\\1", destination)
    return f"<{escaped}>" if " " in escaped else escaped


def _escape_link_title(title: str) -> str:
    return title.replace('"', '\\"')


def build_commonmark_rules() -> dict[str, Rule]:
    """Return a fresh CommonMark rule set (referenceLink keeps mutable state)."""

    rules: dict[str, Rule] = {}

    rules["paragraph"] = {
        "filter": "p",
        "replacement": lambda content, node=None, options=None: "\n\n" + content + "\n\n",
    }

    rules["lineBreak"] = {
        "filter": "br",
        "replacement": lambda content, node, options: options["br"] + "\n",
    }

    def heading_replacement(content: str, node: Any, options: dict[str, Any]) -> str:
        h_level = int(node.node_name[1])
        if options["heading_style"] == "setext" and h_level < 3:
            underline = repeat("=" if h_level == 1 else "-", len(content))
            return f"\n\n{content}\n{underline}\n\n"
        return f"\n\n{repeat('#', h_level)} {content}\n\n"

    rules["heading"] = {
        "filter": ["h1", "h2", "h3", "h4", "h5", "h6"],
        "replacement": heading_replacement,
    }

    def blockquote_replacement(content: str, node: Any = None, options: Any = None) -> str:
        content = re.sub(r"^", "> ", trim_newlines(content), flags=re.MULTILINE)
        return f"\n\n{content}\n\n"

    rules["blockquote"] = {"filter": "blockquote", "replacement": blockquote_replacement}

    def list_replacement(content: str, node: Any, options: Any = None) -> str:
        parent = node.parent_node
        if (
            parent is not None
            and parent.node_name == "LI"
            and parent.last_element_child == node
        ):
            return "\n" + content
        return f"\n\n{content}\n\n"

    rules["list"] = {"filter": ["ul", "ol"], "replacement": list_replacement}

    def list_item_replacement(content: str, node: Any, options: dict[str, Any]) -> str:
        prefix = options["bullet_list_marker"] + " "
        parent = node.parent_node
        if parent is not None and parent.node_name == "OL":
            start = parent.get_attribute("start")
            index = parent.children.index(node) if node in parent.children else 0
            prefix = f"{(int(start) + index) if start else index + 1}. "
        is_paragraph = content.endswith("\n")
        content = trim_newlines(content) + ("\n" if is_paragraph else "")
        indent = " " * len(prefix)
        content = re.sub(r"\n", "\n" + indent, content)
        suffix = "\n" if node.next_sibling else ""
        return prefix + content + suffix

    rules["listItem"] = {"filter": "li", "replacement": list_item_replacement}

    def indented_code_filter(node: Any, options: dict[str, Any]) -> bool:
        return (
            options["code_block_style"] == "indented"
            and node.node_name == "PRE"
            and node.first_child is not None
            and node.first_child.node_name == "CODE"
        )

    def indented_code_replacement(
        content: str, node: Any, options: dict[str, Any]
    ) -> str:
        code = (node.first_child.text_content or "").replace("\n", "\n    ")
        return f"\n\n    {code}\n\n"

    rules["indentedCodeBlock"] = {
        "filter": indented_code_filter,
        "replacement": indented_code_replacement,
    }

    def fenced_code_filter(node: Any, options: dict[str, Any]) -> bool:
        return (
            options["code_block_style"] == "fenced"
            and node.node_name == "PRE"
            and node.first_child is not None
            and node.first_child.node_name == "CODE"
        )

    def fenced_code_replacement(
        content: str, node: Any, options: dict[str, Any]
    ) -> str:
        class_name = node.first_child.get_attribute("class") or ""
        m = re.search(r"language-(\S+)", class_name)
        language = m.group(1) if m else ""
        code = node.first_child.text_content or ""
        fence_char = options["fence"][0]
        fence_size = 3
        for match in re.finditer("^" + re.escape(fence_char) + "{3,}", code, re.M):
            if len(match.group(0)) >= fence_size:
                fence_size = len(match.group(0)) + 1
        fence = repeat(fence_char, fence_size)
        code = re.sub(r"\n$", "", code)
        return f"\n\n{fence}{language}\n{code}\n{fence}\n\n"

    rules["fencedCodeBlock"] = {
        "filter": fenced_code_filter,
        "replacement": fenced_code_replacement,
    }

    rules["horizontalRule"] = {
        "filter": "hr",
        "replacement": lambda content, node, options: f"\n\n{options['hr']}\n\n",
    }

    def inline_link_filter(node: Any, options: dict[str, Any]) -> bool:
        return (
            options["link_style"] == "inlined"
            and node.node_name == "A"
            and bool(node.get_attribute("href"))
        )

    def inline_link_replacement(
        content: str, node: Any, options: Any = None
    ) -> str:
        href = _escape_link_destination(node.get_attribute("href") or "")
        title = _escape_link_title(_clean_attribute(node.get_attribute("title")))
        title_part = f' "{title}"' if title else ""
        return f"[{content}]({href}{title_part})"

    rules["inlineLink"] = {
        "filter": inline_link_filter,
        "replacement": inline_link_replacement,
    }

    references: list[str] = []

    def reference_link_filter(node: Any, options: dict[str, Any]) -> bool:
        return (
            options["link_style"] == "referenced"
            and node.node_name == "A"
            and bool(node.get_attribute("href"))
        )

    def reference_link_replacement(
        content: str, node: Any, options: dict[str, Any]
    ) -> str:
        href = _escape_link_destination(node.get_attribute("href") or "")
        title = _clean_attribute(node.get_attribute("title"))
        if title:
            title = f' "{_escape_link_title(title)}"'
        style = options["link_reference_style"]
        if style == "collapsed":
            replacement = f"[{content}][]"
            reference = f"[{content}]: {href}{title}"
        elif style == "shortcut":
            replacement = f"[{content}]"
            reference = f"[{content}]: {href}{title}"
        else:
            ref_id = len(references) + 1
            replacement = f"[{content}][{ref_id}]"
            reference = f"[{ref_id}]: {href}{title}"
        references.append(reference)
        return replacement

    def reference_link_append(options: dict[str, Any]) -> str:
        nonlocal references
        if not references:
            return ""
        out = "\n\n" + "\n".join(references) + "\n\n"
        references = []
        return out

    rules["referenceLink"] = {
        "filter": reference_link_filter,
        "replacement": reference_link_replacement,
        "append": reference_link_append,
        "references": references,
    }

    def emphasis_replacement(
        content: str, node: Any, options: dict[str, Any]
    ) -> str:
        if not content.strip():
            return ""
        d = options["em_delimiter"]
        return f"{d}{content}{d}"

    rules["emphasis"] = {
        "filter": ["em", "i"],
        "replacement": emphasis_replacement,
    }

    def strong_replacement(
        content: str, node: Any, options: dict[str, Any]
    ) -> str:
        if not content.strip():
            return ""
        d = options["strong_delimiter"]
        return f"{d}{content}{d}"

    rules["strong"] = {
        "filter": ["strong", "b"],
        "replacement": strong_replacement,
    }

    def code_filter(node: Any, options: Any = None) -> bool:
        has_siblings = node.previous_sibling is not None or node.next_sibling is not None
        parent = node.parent_node
        is_code_block = (
            parent is not None and parent.node_name == "PRE" and not has_siblings
        )
        return node.node_name == "CODE" and not is_code_block

    def code_replacement(content: str, node: Any = None, options: Any = None) -> str:
        if not content:
            return ""
        content = re.sub(r"\r?\n|\r", " ", content)
        extra_space = (
            " " if re.search(r"^`|^ .*?[^ ].* $|`$", content) else ""
        )
        delimiter = "`"
        matches = re.findall(r"`+", content) or []
        while delimiter in matches:
            delimiter += "`"
        return f"{delimiter}{extra_space}{content}{extra_space}{delimiter}"

    rules["code"] = {"filter": code_filter, "replacement": code_replacement}

    def image_replacement(content: str, node: Any, options: Any = None) -> str:
        alt = escape_markdown(_clean_attribute(node.get_attribute("alt")))
        src = _escape_link_destination(node.get_attribute("src") or "")
        title = _clean_attribute(node.get_attribute("title"))
        title_part = f' "{_escape_link_title(title)}"' if title else ""
        return f"![{alt}]({src}{title_part})" if src else ""

    rules["image"] = {"filter": "img", "replacement": image_replacement}

    return rules
