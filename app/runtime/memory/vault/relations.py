"""Typed edges between pages, kept as a small Markdown block under the title.

    <!-- relations -->
    - runs_on: [[tool/omaxim]]
    <!-- /relations -->
"""
from __future__ import annotations

import re

from . import doc

# Forward label -> how the edge reads from the other end.
VOCAB = {
    'part_of': 'has part', 'uses': 'used by', 'runs_on': 'hosts', 'depends_on': 'needed by',
    'owns': 'owned by', 'works_at': 'employs', 'located_in': 'contains', 'manages': 'managed by',
    'about': 'subject of', 'related_to': 'related to',
}
BLOCK = re.compile(r'(?ms)^<!-- relations -->\n.*?^<!-- /relations -->\n?')
_LINE = re.compile(r'^- ([a-z_]+): \[\[([^\[\]\n]+)\]\][ \t]*$', re.M)


def parse(page: doc.Document) -> list[tuple[str, str]]:
    block = BLOCK.search(page.preamble)
    if not block:
        return []
    # Unknown labels a person typed by hand survive rewrites; only writes are checked.
    return list(dict.fromkeys((rel, dst.strip()) for rel, dst in _LINE.findall(block.group())))


def render(pairs: list[tuple[str, str]]) -> str:
    if not pairs:
        return ''
    return '<!-- relations -->\n' + '\n'.join(f'- {rel}: [[{dst}]]' for rel, dst in pairs) + '\n<!-- /relations -->'


def store(page: doc.Document, pairs: list[tuple[str, str]]) -> None:
    """Rewrite the block right under the title; facts and other preamble lines stay put."""
    entries = page.entries
    lines = BLOCK.sub('', page.preamble).rstrip().splitlines()
    at = next((i + 1 for i, line in enumerate(lines) if line.startswith('# ')), 0)
    block = render(list(dict.fromkeys(pairs)))
    if block:
        lines[at:at] = block.splitlines()
    page.body = '\n'.join(lines) + ('\n' + '\n'.join('§ ' + e for e in entries) if entries else '')
