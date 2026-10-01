"""Small, deterministic Markdown/frontmatter codec for vault documents."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

_LINK = re.compile(r'\[\[([^\[\]\n]+)\]\]')

@dataclass
class Document:
    meta: dict[str, str | list[str]] = field(default_factory=dict)
    body: str = ''

    @property
    def preamble(self) -> str:
        return re.split(r'(?m)^§', self.body, maxsplit=1)[0].rstrip()

    @property
    def entries(self) -> list[str]:
        return [part.strip() for part in re.findall(r'(?ms)^§[ \t]*(.*?)(?=^§|\Z)', self.body) if part.strip()]


def parse(raw: str) -> Document:
    if not raw.startswith('---\n'):
        return Document(body=raw.strip())
    head, sep, body = raw[4:].partition('\n---\n')
    if not sep:
        raise ValueError('unterminated frontmatter')
    meta: dict[str, str | list[str]] = {}
    for line in head.splitlines():
        key, colon, value = line.partition(':')
        if not colon or not re.fullmatch(r'[a-z_]+', key):
            raise ValueError('invalid frontmatter')
        value = value.strip()
        if value.startswith('[') and value.endswith(']'):
            meta[key] = [v.strip().strip('"\'') for v in value[1:-1].split(',') if v.strip()]
        else:
            meta[key] = value
    return Document(meta, body.strip())


def serialize(doc: Document) -> str:
    lines = ['---']
    for key, value in doc.meta.items():
        if isinstance(value, list):
            value = '[' + ', '.join(value) + ']'
        lines.append(f'{key}: {value}')
    return '\n'.join(lines) + '\n---\n' + (doc.body.strip() + '\n' if doc.body.strip() else '')


def links(text: str) -> list[str]:
    return list(dict.fromkeys(m.group(1).strip() for m in _LINK.finditer(text)))


def encode_fact(text: str) -> str:
    """Escape section markers inside a fact, preserving existing backslashes."""
    text = re.sub(r'^(\\*~~)', lambda m: "\\" + m.group(1), text)
    return re.sub(r'(?m)^(\\*§)', lambda m: "\\" + m.group(1), text)


def fact_data(entry: str) -> dict:
    """Decode optional provenance; old Markdown remains readable."""
    gone = entry.startswith('~~')
    text = re.sub(r'^~~(.*)~~ superseded \d{4}-\d{2}-\d{2}$', r'\1', entry, flags=re.S) if gone else entry
    source = re.search(r'\s*\(src: \[\[([^\]]+)\]\]\)\s*$', text)
    if source:
        text = text[:source.start()]
    origin = re.search(r'\s*\(origin: (agent|consolidation|user|extraction)\)\s*$', text)
    if origin:
        text = text[:origin.start()]
    text = re.sub(r'^\\(\\*~~)', r'\1', text)
    text = re.sub(r'(?m)^\\(\\*§)', r'\1', text)
    return {'text': text.strip(), 'source': source.group(1) if source else '',
            'origin': origin.group(1) if origin else ('consolidation' if source and '#consolidated' in source.group(1) else 'agent'),
            'superseded': gone}
