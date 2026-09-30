"""Structured reading of timeline day notes for the Memory page.

A day note is a run of turn blocks written by ``write.append_timeline``::

    ## 14:02 · session ses_abc · agent main
    - Goal: what the user asked
    - Outcome: what came of it

Older notes may be bare bullets with no heading; each becomes its own untimed
entry so nothing written before this format is lost.
"""
from __future__ import annotations

import re

from . import doc

_HEAD = re.compile(r'^##\s+(\d{1,2}:\d{2})?\s*(?:·\s*session\s+(\S+))?\s*(?:·\s*agent\s+(\S+))?\s*$')


def entries(body: str) -> list[dict]:
    out: list[dict] = []
    cur: dict | None = None
    for line in (body or '').splitlines():
        head = _HEAD.match(line.strip())
        if head:
            cur = {'time': head.group(1) or '', 'session': head.group(2) or '', 'agent': head.group(3) or '',
                   'goal': '', 'outcome': '', 'notes': []}
            out.append(cur)
            continue
        if not line.startswith('- '):
            continue
        if cur is None or not cur['time'] and not cur['session']:
            cur = {'time': '', 'session': '', 'agent': '', 'goal': '', 'outcome': '', 'notes': []}
            out.append(cur)
        text = line[2:].strip()
        label, colon, rest = text.partition(':')
        if colon and label in ('Goal', 'Outcome') and not cur[label.lower()]:
            cur[label.lower()] = rest.strip()
        else:
            cur['notes'].append(text)
    for entry in out:
        entry['links'] = doc.links(' '.join([entry['goal'], entry['outcome'], *entry['notes']]))
    return [e for e in out if e['goal'] or e['outcome'] or e['notes']]


def text_of(entry: dict) -> str:
    return ' '.join([entry['goal'], entry['outcome'], *entry['notes']])


def matches(entry: dict, words: list[str]) -> bool:
    hay = text_of(entry).casefold()
    return all(w in hay for w in words)
