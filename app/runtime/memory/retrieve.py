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
    conn: Any,
    query: str,
    *,
    limit: int = 5,
    user_id: str | None = None,
) -> list[dict[str, Any]]:
    """Hybrid KB search: FTS + semantic (+ lexical fallback), confidence-ranked.

    When ``user_id`` is set, only that account's knowledge rows are returned.
    """
    from app.models.mixins import knowledge_entries as kb
    from app.runtime.memory import embeddings as emb
    from app.runtime.memory import fts

    text = (query or "").strip()
    if not text:
        return []
    k = max(1, min(int(limit or 5), 20))

    fts_ids = fts.search_knowledge_fts(conn, text, limit=k * 2)
    sem_ids = [
        rid for rid, _ in emb.semantic_rank(conn, scope="knowledge", query=text, limit=k * 2)
    ]

    fused = _rrf_fuse([fts_ids, sem_ids], limit=k * 2)
    hits: list[dict[str, Any]] = []
    for eid in fused:
        entry = kb.get_entry(conn, eid, user_id=user_id)
        if entry:
            hits.append(entry)
    if not hits:
        hits = kb.search_entries_lexical(conn, text, limit=k * 2, user_id=user_id)
    else:
        # Drop other-users' FTS/sem hits when scoping.
        if user_id is not None:
            hits = [
                h
                for h in hits
                if (h.get("user_id") or "web") == (user_id or "web")
            ]
        if not hits:
            hits = kb.search_entries_lexical(conn, text, limit=k * 2, user_id=user_id)

    # Prefer high-confidence semantic knowledge among fused hits.
    ranked = kb.rank_entries_by_confidence(hits, limit=k)
    return ranked


def search_messages_hybrid(
    conn: Any,
    query: str,
    *,
    limit: int = 10,
    user_id: str | None = None,
) -> list[dict[str, Any]]:
    from app.runtime.memory import fts

    text = (query or "").strip()
    if not text:
        return []
    k = max(1, min(int(limit or 10), 50))
    msg_ids = fts.search_messages_fts(conn, text, limit=max(k * 3, k))
    if msg_ids:
        placeholders = ",".join("?" for _ in msg_ids)
        if user_id is None:
            rows = conn.execute(
                f"SELECT session_id, type, content, agent_id, function, ts, id "
                f"FROM messages WHERE id IN ({placeholders})",
                msg_ids,
            ).fetchall()
        else:
            rows = conn.execute(
                f"SELECT m.session_id, m.type, m.content, m.agent_id, m.function, "
                f"m.ts, m.id FROM messages m "
                f"JOIN sessions s ON s.id = m.session_id "
                f"WHERE m.id IN ({placeholders}) AND s.user_id = ?",
                [*msg_ids, user_id],
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
            if len(out) >= k:
                break
        if out:
            return out

    # LIKE fallback
    from app.models.mixins import messages as msg_mod

    return msg_mod.search_messages_like(conn, text, limit=k, user_id=user_id)


def retrieve_for_turn(
    query: str,
    *,
    agent_id: str | None = None,
    session_id: str | None = None,
    user_id: str | None = None,
    limit: int = 4,
) -> str:
    """Build a compact memory block for system-prompt injection (Reuse step).

    Ranking preference (Learning OS Slice 2):
    user prefs → bound project → high-confidence semantic KB → rest.

    Knowledge and USER.md are scoped to ``user_id`` (turn-bound account).
    """
    if not (query or "").strip():
        return ""
    try:
        from app.services import store
    except Exception:
        return ""

    uid = user_id
    if uid is None:
        try:
            from app.runtime.tools.user_ctx import current_user_id

            uid = current_user_id()
        except Exception:
            uid = "web"

    parts: list[str] = []

    # 1) User lane (prefs / style) — highest priority retrieval signal.
    try:
        from app.runtime.memory import curated

        user_entries = curated.read_user_entries(user_id=uid)
        cleaned = [e.strip() for e in user_entries if (e or "").strip()]
        if cleaned:
            snippet = "\n".join(f"- {e[:160]}" for e in cleaned[:4])
            parts.append("User prefs [user]:\n" + snippet)
    except Exception as exc:
        _logger.debug("user retrieve failed: %s", exc)

    # 2) Project lane when workplace is bound.
    if agent_id:
        try:
            from app.runtime.memory import project as project_mem

            wid = project_mem.workplace_id_for_agent(agent_id)
            if wid:
                snip = project_mem.format_snippet(wid, limit=400)
                if snip and snip != "(empty)":
                    parts.append(f"Project notes [project] ({wid}):\n{snip}")
        except Exception as exc:
            _logger.debug("project retrieve failed: %s", exc)

    # 3) Concrete past experiences (episodic), then semantic KB.
    try:
        episodes = store.search_episodes(query, limit=max(2, limit // 2 or 2), user_id=uid)
        if episodes:
            lines = []
            for ep in episodes:
                summary = (ep.get("summary") or ep.get("title") or "").strip().replace(
                    "\n", " "
                )
                if len(summary) > 200:
                    summary = summary[:197] + "…"
                lines.append(f"- {ep.get('title') or ep.get('id')}: {summary}")
            parts.append("Past experiences [episodic]:\n" + "\n".join(lines))
    except Exception as exc:
        _logger.debug("episodic retrieve failed: %s", exc)

    try:
        kb_hits = store.search_knowledge(query, limit=limit, user_id=uid)
        if kb_hits:
            lines = []
            for h in kb_hits[:limit]:
                body = (h.get("body") or "").strip().replace("\n", " ")
                if len(body) > 180:
                    body = body[:177] + "…"
                conf = h.get("confidence")
                conf_s = f" conf={conf:.2f}" if isinstance(conf, (int, float)) else ""
                lines.append(f"- {h.get('title')}: {body}{conf_s}")
                try:
                    eid = (h.get("id") or "").strip()
                    if eid:
                        store.bump_knowledge_use(eid)
                except Exception:
                    pass
            parts.append("Knowledge [semantic]:\n" + "\n".join(lines))
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
                parts.append("Agent state [agent]:\n" + "\n".join(lines))
        except Exception:
            pass

    if session_id:
        try:
            summary = store.get_session_summary(session_id)
            if summary and summary.get("summary"):
                text = summary["summary"].strip()
                if len(text) > 400:
                    text = text[:397] + "…"
                parts.append(f"Session memory [conversation]:\n{text}")
        except Exception:
            pass
        try:
            from app.models.mixins import swarm_notes as sn

            shared = store.with_db(
                lambda conn: sn.format_swarm_notes_snippet(
                    conn, session_id=session_id, limit=5
                )
            )
            if shared:
                parts.append(f"Shared swarm notes [shared]:\n{shared}")
        except Exception as exc:
            _logger.debug("swarm notes retrieve failed: %s", exc)

    try:
        arts = store.search_artifacts(query, limit=3, session_id=session_id)
        if arts:
            lines = [
                f"- {a.get('title')} ({a.get('path') or a.get('kind')})"
                for a in arts
            ]
            parts.append("Artifacts [execution]:\n" + "\n".join(lines))
    except Exception:
        pass

    try:
        exec_hits = store.search_execution_snippets(
            query, session_id=session_id, limit=3
        )
        if exec_hits:
            lines = [
                f"- {h.get('title')}: {(h.get('snippet') or '')[:160]}"
                for h in exec_hits
            ]
            parts.append("Execution snippets [execution]:\n" + "\n".join(lines))
    except Exception as exc:
        _logger.debug("execution snippet retrieve failed: %s", exc)

    if not parts:
        return ""
    return (
        "## Retrieved memory\n"
        "Prefer user prefs and high-confidence knowledge; verify with tools.\n\n"
        + "\n\n".join(parts)
    )


__all__ = [
    "search_knowledge_hybrid",
    "search_messages_hybrid",
    "retrieve_for_turn",
]
