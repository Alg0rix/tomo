"""Persistent memory CRUD and search over the account's Markdown vault."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from app.runtime.memory.vault import doc, paths, read, write


def run(arguments: dict[str, Any]) -> str:
    from app.services import store
    from app.runtime.tools.user_ctx import current_user_id

    if not isinstance(arguments, dict):
        return "Error: arguments must be an object"
    if "target" in arguments:
        return "Error: use entity=type/slug; memory stores only vault pages"
    action = str(arguments.get("action") or "list").strip().lower()
    key = str(arguments.get("entity") or "").strip()
    from app.runtime.policy import authorize_tool
    authorize_tool("memory", arguments)
    uid = current_user_id()
    try:
        if action == "search":
            query = arguments.get("query")
            if not isinstance(query, str) or not query.strip():
                return "Error: query is required"
            def searching(conn):
                hits = read.search(conn, uid, query, limit=20,
                                   include_superseded=arguments.get("include_superseded") is True)
                return '\n\n'.join(f'[[{h["type"]}/{h["slug"]}]]\n' + '\n'.join(
                    ('[superseded] ' if f['superseded'] else '') + f['text'] +
                    (f" (src: [[{f['source']}]])" if f['source'] else '')
                    for f in h['matched_facts']) + '\n' +
                    read.related_text(conn, uid, [h['path']]) for h in hits)
            return store.with_db(searching) or 'Vault has no matching facts.'
        if action == "list" and not key:
            def listing(conn):
                from app.runtime.memory.vault import index
                index.rebuild(conn, uid)
                pages = conn.execute('SELECT path,type,slug,body FROM vault_docs WHERE user_id=? AND kind="entity" ORDER BY type,slug', (uid,)).fetchall()
                lines = [f'[vault] {len(pages)} pages']
                for page in pages:
                    live = [doc.fact_data(e)['text'] for e in doc.parse(page['body']).entries if not e.startswith('~~')]
                    lines.append(f'[[{page["type"]}/{page["slug"]}]] ' + '; '.join(f[:160] for f in live[:3]))
                    related = read.related_text(conn, uid, [page['path']])
                    if related:
                        lines.append(related)
                return '\n'.join(lines)
            return store.with_db(listing)
        typ, slug = paths.entity_key(key)
        path = paths.entity_path(uid, key)
        if action == 'list':
            if not path.is_file():
                return 'Vault page is empty.'
            def page_listing(conn):
                from app.runtime.memory.vault import index
                index.rebuild(conn, uid)
                related = read.related_text(conn, uid, [f'{uid}/entities/{key}.md'])
                raw = path.read_text(encoding='utf-8')
                return raw + ('\n' + related if related else '')
            return store.with_db(page_listing)
        if action == "add":
            if typ == "person" and (slug in {"me", "user", "self", "the-user", "myself", uid.casefold(), uid.casefold().replace("_", "-")} or slug.startswith("usr-")):
                return "Error: save the user's identity/preferences on user/profile"
            content = arguments.get("content")
            if not isinstance(content, str) or not content.strip():
                return "Error: content is required"
            result = write.add_entity(uid, key, content,
                source=str(arguments.get('source') or paths.timeline_source(uid, datetime.now().astimezone().date().isoformat())),
                aliases=arguments.get("aliases"), supersedes=str(arguments.get("supersedes") or ""))
            if result.get("conflict"):
                return "Error: supersedes must match exactly one live fact"
            return "Saved vault fact." if result["added"] else "Near-duplicate already present."
        if action in {"replace", "remove"}:
            old = arguments.get("old")
            if not isinstance(old, str) or not old.strip():
                return "Error: old (unique substring of a live fact) is required"
            # Resolve and mutate under the same account lock to prevent stale-index edits.
            with write._lock(uid):
                if not path.is_file():
                    return "Error: vault page is empty"
                page = doc.parse(path.read_text(encoding="utf-8"))
                matches = [(i, doc.fact_data(e)["text"]) for i, e in enumerate(page.entries)
                           if not e.startswith("~~") and old in doc.fact_data(e)["text"]]
                if len(matches) != 1:
                    return "Error: old must match exactly one live fact"
                number, expected = matches[0]
                if action == "replace":
                    text = arguments.get("content")
                    if not isinstance(text, str) or not text.strip():
                        return "Error: content is required"
                    ok = write.correct_fact(uid, key, number, text=text, expected=expected)
                else:
                    ok = write.forget_fact(uid, key, number)
            return ("Replaced vault fact." if action == "replace" else "Removed vault fact.") if ok else "Error: fact changed; list the page and retry"
        return "Error: action must be add, list, search, replace, or remove"
    except (ValueError, OSError) as exc:
        return f"Error: {exc}"
