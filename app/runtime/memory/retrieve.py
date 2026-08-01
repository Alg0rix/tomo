"""Hybrid retrieval — FTS lexical + optional semantic embeddings."""

from __future__ import annotations

import logging
from typing import Any

_logger = logging.getLogger(__name__)


def _rrf_fuse(
    ranked_lists: list[list[str]], *, k: int = 60, limit: int = 5
) -> list[str]:
    """Reciprocal rank fusion across id lists."""
    scores: dict[str, float] = {}
    for ranked in ranked_lists:
        for i, rid in enumerate(ranked):
            if not rid:
                continue
            scores[rid] = scores.get(rid, 0.0) + 1.0 / (k + i + 1)
    ordered = sorted(scores.items(), key=lambda p: -p[1])
    return [rid for rid, _ in ordered[: max(1, min(limit, 20))]]


def search_knowledge_hybrid(
    conn: Any, query: str, *, limit: int = 5
) -> list[dict[str, Any]]:
    """Hybrid KB search: FTS + semantic (+ lexical fallback)."""
    from app.models.mixins import knowledge_entries as kb
    from app.runtime.memory import embeddings as emb
    from app.runtime.memory import fts

    text = (query or "").strip()
    if not text:
        return []
    k = max(1, min(int(limit or 5), 20))

    fts_ids = fts.search_knowledge_fts(conn, text, limit=k * 2)
    sem_ids = [rid for rid, _ in emb.semantic_rank(conn, scope="knowledge", query=text, limit=k * 2)]

    fused = _rrf_fuse([fts_ids, sem_ids], limit=k)
    hits: list[dict[str, Any]] = []
    for eid in fused:
        entry = kb.get_entry(conn, eid)
        if entry:
            hits.append(entry)
    if hits:
        return hits

    # Fallback: legacy token scorer (always works).
    return kb.search_entries_lexical(conn, text, limit=k)


def search_messages_hybrid(
    conn: Any, query: str, *, limit: int = 10
) -> list[dict[str, Any]]:
    from app.runtime.memory import fts

    text = (query or "").strip()
    if not text:
        return []
    k = max(1, min(int(limit or 10), 50))
    msg_ids = fts.search_messages_fts(conn, text, limit=k)
    if msg_ids:
        placeholders = ",".join("?" for _ in msg_ids)
        rows = conn.execute(
            f"SELECT session_id, type, content, agent_id, function, ts, id "
            f"FROM messages WHERE id IN ({placeholders})",
            msg_ids,
        ).fetchall()
        by_id = {int(r["id"]): r for r in rows}
        out = []
        for mid in msg_ids:
            r = by_id.get(mid)
            if not r:
                continue
            out.append(
                {
                    "session_id": r["session_id"],
                    "type": r["type"],
                    "content": r["content"] or "",
                    "agent_id": r["agent_id"],
                    "function": r["function"],
                    "ts": r["ts"],
                }
            )
        if out:
            return out

    # LIKE fallback
    from app.models.mixins import messages as msg_mod

    return msg_mod.search_messages_like(conn, text, limit=k)


def retrieve_for_turn(
    query: str,
    *,
    agent_id: str | None = None,
    session_id: str | None = None,
    limit: int = 4,
) -> str:
    """Build a compact memory block for system-prompt injection (Reuse step)."""
    if not (query or "").strip():
        return ""
    try:
        from app.services import store
    except Exception:
        return ""

    parts: list[str] = []
    try:
        kb_hits = store.search_knowledge(query, limit=limit)
        if kb_hits:
            lines = []
            for h in kb_hits[:limit]:
                body = (h.get("body") or "").strip().replace("\n", " ")
                if len(body) > 180:
                    body = body[:177] + "…"
                lines.append(f"- {h.get('title')}: {body}")
            parts.append("Knowledge:\n" + "\n".join(lines))
    except Exception as exc:
        _logger.debug("kb retrieve failed: %s", exc)

    try:
        skills = store.list_skills()
        q = query.lower()
        scored: list[tuple[int, dict[str, Any]]] = []
        for s in skills:
            if not s.get("enabled", True):
                continue
            blob = f"{s.get('name','')} {s.get('description','')}".lower()
            score = sum(1 for tok in q.split() if len(tok) > 2 and tok in blob)
            if score:
                scored.append((score, s))
        scored.sort(key=lambda p: -p[0])
        if scored:
            lines = [
                f"- {s['id']}: {s.get('description') or s.get('name')}"
                for _, s in scored[:3]
            ]
            parts.append(
                "Relevant skills (call use_skill to load):\n" + "\n".join(lines)
            )
    except Exception as exc:
        _logger.debug("skill retrieve failed: %s", exc)

    if agent_id:
        try:
            state = store.list_agent_state(agent_id)
            if state:
                lines = [f"- {k}: {v}" for k, v in list(state.items())[:6]]
                parts.append("Agent state:\n" + "\n".join(lines))
        except Exception:
            pass

    if session_id:
        try:
            summary = store.get_session_summary(session_id)
            if summary and summary.get("summary"):
                text = summary["summary"].strip()
                if len(text) > 400:
                    text = text[:397] + "…"
                parts.append(f"Session memory:\n{text}")
        except Exception:
            pass

    try:
        arts = store.search_artifacts(query, limit=3, session_id=session_id)
        if arts:
            lines = [
                f"- {a.get('title')} ({a.get('path') or a.get('kind')})"
                for a in arts
            ]
            parts.append("Artifacts:\n" + "\n".join(lines))
    except Exception:
        pass

    if not parts:
        return ""
    return (
        "## Retrieved memory\n"
        "Use only if relevant; prefer tools to verify.\n\n"
        + "\n\n".join(parts)
    )


__all__ = [
    "search_knowledge_hybrid",
    "search_messages_hybrid",
    "retrieve_for_turn",
]
