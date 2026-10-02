"""Non-destructive account merge: validate, snapshot, union, reindex.

Original Telegram documents are retained. Snapshots preserve both accounts
before the first merge, including conflicting pages and superseded facts.
"""
from __future__ import annotations

import os
import re
import shutil
import tempfile
from contextlib import ExitStack
from pathlib import Path

from app.core import home
from . import doc, index, paths, write


def merge_accounts(conn, source_id: str, target_id: str, *, home_root: Path | None = None) -> dict:
    source = paths.vault_root(source_id, home_root=home_root)
    target = paths.vault_root(target_id, home_root=home_root)
    backup = home._root(home_root) / 'state' / 'telegram-memory-backups' / f'{source_id}-to-{target_id}'
    plan: list[tuple[Path, str]] = []
    with ExitStack() as stack:
        for uid in sorted([source_id, target_id]):
            stack.enter_context(write._lock(uid))
        files = sorted([*source.glob('entities/*/*.md'), *source.glob('timeline/*/*/*.md')])
        for path in files:
            rel = paths.relative_doc(source, path)
            incoming = doc.parse(path.read_text(encoding='utf-8').replace(f'[[{source_id}/', f'[[{target_id}/'))
            if rel.startswith('entities/'):
                dest = paths.entity_path(target_id, f'{path.parent.name}/{path.stem}', home_root=home_root)
            else:
                # Validate the complete source layout, not only its date.
                if path != paths.timeline_path(source_id, path.stem, home_root=home_root):
                    raise ValueError('invalid timeline path')
                dest = paths.timeline_path(target_id, path.stem, home_root=home_root)
            if dest.is_file():
                page = doc.parse(dest.read_text(encoding='utf-8'))
                if rel.startswith('entities/'):
                    entries = page.entries
                    # Include retired entries in deduplication: never resurrect
                    # a fact the account has already corrected or forgotten.
                    seen = {doc.fact_data(e)['text'].casefold() for e in entries}
                    for entry in incoming.entries:
                        key = doc.fact_data(entry)['text'].casefold()
                        if key not in seen:
                            entries.append(entry)
                            seen.add(key)
                    if incoming.preamble and incoming.preamble not in page.preamble:
                        page.body = page.preamble + '\n\n' + incoming.preamble
                    write._body(page, entries)
                    for field in ('aliases', 'tags'):
                        old, new = page.meta.get(field, []), incoming.meta.get(field, [])
                        if not isinstance(old, list) or not isinstance(new, list):
                            raise ValueError('invalid vault metadata')
                        page.meta[field] = list(dict.fromkeys([*old, *new]))
                else:
                    blocks = [b.strip() for b in re.split(r'(?m)(?=^## )', incoming.body) if b.strip()]
                    for block in blocks:
                        if block not in page.body:
                            page.body = (page.body.rstrip() + '\n\n' + block).strip()
                    page.meta['consolidated'] = 'false'
                incoming = page
            plan.append((dest, doc.serialize(incoming)))
        if files:
            backup.mkdir(parents=True, exist_ok=True)
            for name, root in [('telegram', source), ('account', target)]:
                snapshot = backup / name
                if root.exists() and not snapshot.exists():
                    # Publish only complete snapshots; an interrupted copy must
                    # not make a retry mistake a partial backup for a good one.
                    with tempfile.TemporaryDirectory(dir=backup, prefix='.snapshot-') as temp:
                        staging = Path(temp) / name
                        shutil.copytree(root, staging, symlinks=True)
                        os.replace(staging, snapshot)
            for dest, content in plan:
                write.atomic_write(dest, content)
        index.rebuild(conn, target_id, home_root=home_root)
    return {'documents': len(plan), 'backup': str(backup) if files else None}
