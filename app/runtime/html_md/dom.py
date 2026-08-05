"""Minimal DOM adapter over BeautifulSoup for HTML→Markdown conversion."""

from __future__ import annotations

from typing import Any, Iterator
from weakref import WeakKeyDictionary

from bs4 import BeautifulSoup, NavigableString, PageElement, Tag

ELEMENT_NODE = 1
TEXT_NODE = 3
DOCUMENT_NODE = 9
DOCUMENT_FRAGMENT_NODE = 11

# One wrapper per underlying BS4 object so Node annotations (is_code etc.) stick.
_WRAPPERS: WeakKeyDictionary[Any, "DomNode"] = WeakKeyDictionary()


class DomNode:
    """Thin wrapper giving BS4 nodes a DOM-like face."""

    __slots__ = ("_el", "_cache", "__weakref__")

    def __init__(self, el: PageElement) -> None:
        self._el = el
        self._cache: dict[str, Any] = {}

    @classmethod
    def wrap(cls, el: PageElement | None) -> DomNode | None:
        if el is None:
            return None
        existing = _WRAPPERS.get(el)
        if existing is not None:
            return existing
        node = cls(el)
        try:
            _WRAPPERS[el] = node
        except TypeError:
            pass
        return node

    @property
    def node_type(self) -> int:
        if isinstance(self._el, NavigableString):
            return TEXT_NODE
        return ELEMENT_NODE

    @property
    def node_name(self) -> str:
        if isinstance(self._el, NavigableString):
            return "#text"
        name = getattr(self._el, "name", None) or ""
        return str(name).upper()

    @property
    def node_value(self) -> str:
        if isinstance(self._el, NavigableString):
            return str(self._el)
        return ""

    @property
    def data(self) -> str:
        return self.node_value

    @data.setter
    def data(self, value: str) -> None:
        if isinstance(self._el, NavigableString):
            new = type(self._el)(value)
            self._el.replace_with(new)
            old = self._el
            self._el = new
            _WRAPPERS.pop(old, None)
            try:
                _WRAPPERS[new] = self
            except TypeError:
                pass

    @property
    def text_content(self) -> str:
        if isinstance(self._el, NavigableString):
            return str(self._el)
        assert isinstance(self._el, Tag)
        return self._el.get_text()

    @property
    def outer_html(self) -> str:
        if isinstance(self._el, NavigableString):
            return str(self._el)
        return str(self._el)

    @property
    def parent_node(self) -> DomNode | None:
        parent = self._el.parent
        if parent is None or isinstance(parent, BeautifulSoup):
            return None
        if not isinstance(parent, (Tag, NavigableString)):
            return None
        return DomNode.wrap(parent)

    @property
    def previous_sibling(self) -> DomNode | None:
        return DomNode.wrap(self._el.previous_sibling)

    @property
    def next_sibling(self) -> DomNode | None:
        return DomNode.wrap(self._el.next_sibling)

    @property
    def first_child(self) -> DomNode | None:
        if not isinstance(self._el, Tag):
            return None
        children = list(self._el.children)
        return DomNode.wrap(children[0]) if children else None

    @property
    def last_child(self) -> DomNode | None:
        if not isinstance(self._el, Tag):
            return None
        children = list(self._el.children)
        return DomNode.wrap(children[-1]) if children else None

    @property
    def last_element_child(self) -> DomNode | None:
        if not isinstance(self._el, Tag):
            return None
        kids = [c for c in self._el.children if isinstance(c, Tag)]
        return DomNode.wrap(kids[-1]) if kids else None

    @property
    def child_nodes(self) -> list[DomNode]:
        if not isinstance(self._el, Tag):
            return []
        out: list[DomNode] = []
        for c in self._el.children:
            wrapped = DomNode.wrap(c)
            if wrapped is not None:
                out.append(wrapped)
        return out

    @property
    def children(self) -> list[DomNode]:
        if not isinstance(self._el, Tag):
            return []
        out: list[DomNode] = []
        for c in self._el.children:
            if isinstance(c, Tag):
                wrapped = DomNode.wrap(c)
                if wrapped is not None:
                    out.append(wrapped)
        return out

    def get_attribute(self, name: str) -> str | None:
        if not isinstance(self._el, Tag):
            return None
        val = self._el.get(name)
        if val is None:
            return None
        if isinstance(val, list):
            return " ".join(str(v) for v in val)
        return str(val)

    def get_elements_by_tag_name(self, name: str) -> list[DomNode]:
        if not isinstance(self._el, Tag):
            return []
        if name == "*":
            tags = self._el.find_all(True)
        else:
            tags = self._el.find_all(name.lower())
        out: list[DomNode] = []
        for t in tags:
            wrapped = DomNode.wrap(t)
            if wrapped is not None:
                out.append(wrapped)
        return out

    def clone_node(self, deep: bool = True) -> DomNode:
        if isinstance(self._el, NavigableString):
            return DomNode(type(self._el)(str(self._el)))
        assert isinstance(self._el, Tag)
        return DomNode(_deep_copy_tag(self._el))

    def remove_child(self, child: DomNode) -> DomNode:
        child._el.extract()
        return child

    @property
    def is_block(self) -> bool:
        return bool(self._cache.get("is_block", False))

    @is_block.setter
    def is_block(self, value: bool) -> None:
        self._cache["is_block"] = value

    @property
    def is_code(self) -> bool:
        return bool(self._cache.get("is_code", False))

    @is_code.setter
    def is_code(self, value: bool) -> None:
        self._cache["is_code"] = value

    @property
    def is_blank(self) -> bool:
        return bool(self._cache.get("is_blank", False))

    @is_blank.setter
    def is_blank(self, value: bool) -> None:
        self._cache["is_blank"] = value

    @property
    def flanking_whitespace(self) -> dict[str, str]:
        return self._cache.get("flanking_whitespace", {"leading": "", "trailing": ""})

    @flanking_whitespace.setter
    def flanking_whitespace(self, value: dict[str, str]) -> None:
        self._cache["flanking_whitespace"] = value

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, DomNode):
            return NotImplemented
        return self._el is other._el

    def __hash__(self) -> int:
        return id(self._el)


def _deep_copy_tag(tag: Tag) -> Tag:
    soup = BeautifulSoup(str(tag), "html.parser")
    cloned = soup.find(True)
    assert cloned is not None
    return cloned  # type: ignore[return-value]


def parse_html_fragment(html: str) -> DomNode:
    """Parse HTML into a single root element for conversion."""
    wrapped = f'<html-md-root id="html-md-root">{html}</html-md-root>'
    soup = BeautifulSoup(wrapped, "html.parser")
    root = soup.find(id="html-md-root")
    if root is None:
        root = soup.body or soup
    node = DomNode.wrap(root)
    assert node is not None
    return node


def iter_descendants(node: DomNode) -> Iterator[DomNode]:
    for child in node.child_nodes:
        yield child
        if child.node_type == ELEMENT_NODE:
            yield from iter_descendants(child)
