"""CommonMark to Telegram-safe HTML, with independently valid message chunks."""

from __future__ import annotations

import html
import re
from urllib.parse import urlsplit

from markdown_it import MarkdownIt
from markdown_it.token import Token

_MARKDOWN = MarkdownIt("commonmark", {"html": False}).enable(["table", "strikethrough"])
_TAGS = {"strong": "b", "em": "i", "s": "s"}


def utf16_len(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def _inline(
    tokens: list[Token], *, allow_code: bool = True, allow_links: bool = True
) -> str:
    out: list[str] = []
    stack: list[tuple[str, str]] = []
    links: list[tuple[str, bool]] = []
    for token in tokens:
        kind = token.type
        if kind == "text":
            out.append(html.escape(token.content, quote=False))
        elif kind in {"softbreak", "hardbreak"}:
            out.append("\n")
        elif kind == "code_inline":
            code = html.escape(token.content, quote=False)
            if allow_code:
                # Telegram code cannot be nested inside emphasis or links.
                out.extend(f"</{tag}>" for tag, _ in reversed(stack))
                out.append("<code>" + code + "</code>")
                out.extend(opening for _, opening in stack)
            else:
                out.append(code)
        elif token.tag in _TAGS:
            tag = _TAGS[token.tag]
            if token.nesting == 1:
                stack.append((tag, f"<{tag}>"))
                out.append(f"<{tag}>")
            else:
                stack.pop()
                out.append(f"</{tag}>")
        elif kind == "link_open":
            href = token.attrGet("href") or ""
            safe = urlsplit(href).scheme.lower() in {"https", "http", "mailto", "tg"}
            safe = safe and utf16_len(href) <= 1000
            rendered_link = safe and allow_links
            links.append((href if safe else "", rendered_link))
            if rendered_link:
                opening = '<a href="' + html.escape(href, quote=True) + '">'
                stack.append(("a", opening))
                out.append(opening)
        elif kind == "link_close":
            href, rendered_link = links.pop()
            if rendered_link:
                stack.pop()
                out.append("</a>")
            elif href:
                out.append(" (" + html.escape(href, quote=False) + ")")
        elif kind == "image":
            out.append(html.escape(token.content, quote=False))
        else:
            out.append(html.escape(token.content, quote=False))
    return "".join(out)


def render_markdown(text: str) -> str:
    """Render only supported Telegram tags; raw HTML stays literal text."""
    tokens = _MARKDOWN.parse(text)
    out: list[str] = []
    lists: list[int | None] = []
    quote_depth = 0
    heading = False
    index = 0
    while index < len(tokens):
        token = tokens[index]
        kind = token.type
        if kind == "table_open":
            rows: list[list[str]] = []
            row: list[str] = []
            index += 1
            while index < len(tokens) and tokens[index].type != "table_close":
                item = tokens[index]
                if item.type == "tr_open":
                    row = []
                elif item.type == "inline":
                    row.append(
                        _inline(
                            item.children or [],
                            allow_code=quote_depth == 0,
                            allow_links=quote_depth == 0,
                        )
                    )
                elif item.type == "tr_close":
                    rows.append(row)
                index += 1
            if rows:
                headers = rows[0]
                for values in rows[1:]:
                    for col, value in enumerate(values):
                        label = headers[col] if col < len(headers) else str(col + 1)
                        out.append(f"{label}: {value}\n")
                    out.append("\n")
                if len(rows) == 1:
                    out.append(" · ".join(headers) + "\n\n")
        elif kind == "inline":
            out.append(
                _inline(
                    token.children or [],
                    allow_code=quote_depth == 0 and not heading,
                    allow_links=quote_depth == 0,
                )
            )
        elif kind in {"fence", "code_block"}:
            if quote_depth:
                out.append(html.escape(token.content, quote=False) + "\n")
                index += 1
                continue
            language = token.info.strip().split()[0] if token.info.strip() else ""
            language = language if re.fullmatch(r"[\w+-]{1,32}", language) else ""
            opening = f'<code class="language-{language}">' if language else "<code>"
            out.append(
                "<pre>"
                + opening
                + html.escape(token.content, quote=False)
                + "</code></pre>\n\n"
            )
        elif kind == "heading_open":
            heading = True
            out.append("<b>")
        elif kind == "heading_close":
            heading = False
            out.append("</b>\n\n")
        elif kind == "paragraph_close":
            out.append("\n" if token.hidden else "\n\n")
        elif kind == "blockquote_open":
            if quote_depth == 0:
                out.append("<blockquote>")
            quote_depth += 1
        elif kind == "blockquote_close":
            quote_depth -= 1
            if quote_depth == 0:
                out.append("</blockquote>\n\n")
        elif kind in {"bullet_list_open", "ordered_list_open"}:
            lists.append(
                int(token.attrGet("start") or 1)
                if kind == "ordered_list_open"
                else None
            )
        elif kind == "list_item_open":
            counter = lists[-1] if lists else None
            out.append(
                "  " * max(0, len(lists) - 1)
                + (f"{counter}. " if counter is not None else "• ")
            )
            if counter is not None:
                lists[-1] = counter + 1
        elif kind == "list_item_close":
            if out and not out[-1].endswith("\n"):
                out.append("\n")
        elif kind in {"bullet_list_close", "ordered_list_close"}:
            lists.pop()
            out.append("\n")
        elif kind == "hr":
            out.append("─────\n\n")
        index += 1
    return "".join(out).strip()


def split_html(rendered: str, limit: int = 3900) -> list[str]:
    """Split escaped HTML without cutting entities, Unicode, or tag pairs.

    The conservative budget includes markup as well as UTF-16 text, making every
    chunk fit independently even on clients counting escaped text differently.
    """
    chunks: list[str] = []
    stack: list[tuple[str, str]] = []
    parts: list[str] = []
    size = 0
    has_text = False

    def flush() -> None:
        nonlocal parts, size, has_text
        if has_text:
            chunks.append(
                "".join(parts) + "".join(close for _, close in reversed(stack))
            )
        parts = [opening for opening, _ in stack]
        size = sum(utf16_len(p) for p in parts)
        has_text = False

    for match in re.finditer(
        r"</?[^>]+>|&(?:#\d+|#x[0-9a-fA-F]+|\w+);|[^<&]+|[<&]", rendered
    ):
        value = match.group()
        if value.startswith("<"):
            if value.startswith("</"):
                if stack:
                    stack.pop()
                parts.append(value)
                size += utf16_len(value)
            else:
                tag = re.match(r"<(\w+)", value)
                if tag:
                    close = f"</{tag[1]}>"
                    budget = (
                        size
                        + utf16_len(value + close)
                        + sum(utf16_len(c) for _, c in stack)
                    )
                    if budget > limit:
                        flush()
                    stack.append((value, close))
                    parts.append(value)
                    size += utf16_len(value)
            continue
        atoms = [value] if value.startswith("&") else value
        for atom in atoms:
            closing_size = sum(utf16_len(c) for _, c in stack)
            if size + utf16_len(atom) + closing_size > limit:
                flush()
            parts.append(atom)
            size += utf16_len(atom)
            has_text = True
    flush()
    return chunks


def plain_text(rendered: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", rendered))
