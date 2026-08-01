"""Embedding helpers for semantic memory (OpenAI-compatible).

Uses the configured LLM profile's API key/base_url. When unavailable, returns
None and callers fall back to FTS/lexical search only.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import time
from typing import Any

_logger = logging.getLogger(__name__)

DEFAULT_EMBED_MODEL = "text-embedding-3-small"


def _profile() -> dict[str, Any]:
    try:
        from app.services import store

        return store.resolve_llm_profile(None) or {}
    except Exception:
        return {}


def embeddings_available() -> bool:
    p = _profile()
    return bool((p.get("api_key") or "").strip())


def embed_texts(texts: list[str], *, model: str | None = None) -> list[list[float]] | None:
    """Embed one or more texts. Returns None when embeddings are unavailable."""
    cleaned = [(t or "").strip() for t in texts]
    if not any(cleaned):
        return None
    profile = _profile()
    api_key = (profile.get("api_key") or "").strip()
    if not api_key:
        return None
    base_url = (profile.get("base_url") or "https://api.openai.com/v1").rstrip("/")
    use_model = model or DEFAULT_EMBED_MODEL
    try:
        from openai import OpenAI

        client = OpenAI(api_key=api_key, base_url=base_url)
        resp = client.embeddings.create(model=use_model, input=cleaned)
        # Preserve input order
        by_idx = {item.index: list(item.embedding) for item in resp.data}
        return [by_idx[i] for i in range(len(cleaned))]
    except Exception as exc:
        _logger.debug("embed_texts failed: %s", exc)
        return None


def embed_text(text: str, *, model: str | None = None) -> list[float] | None:
    vectors = embed_texts([text], model=model)
    if not vectors:
        return None
    return vectors[0]


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na <= 0 or nb <= 0:
        return 0.0
    return dot / (math.sqrt(na) * math.sqrt(nb))


def text_hash(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:32]


def upsert_embedding(
    conn: Any,
    *,
    scope: str,
    ref_id: str,
    text: str,
    vector: list[float] | None = None,
    model: str = DEFAULT_EMBED_MODEL,
) -> bool:
    """Store embedding for ``scope/ref_id``. Computes vector when omitted."""
    body = (text or "").strip()
    if not body or not ref_id:
        return False
    th = text_hash(body)
    row = conn.execute(
        "SELECT text_hash FROM memory_embeddings WHERE scope=? AND ref_id=?",
        (scope, ref_id),
    ).fetchone()
    if row and row["text_hash"] == th:
        return True
    vec = vector
    if vec is None:
        vec = embed_text(body, model=model)
    if not vec:
        return False
    conn.execute(
        "INSERT INTO memory_embeddings(scope, ref_id, model, dims, vector_json, "
        "text_hash, updated_at) VALUES (?,?,?,?,?,?,?) "
        "ON CONFLICT(scope, ref_id) DO UPDATE SET model=excluded.model, "
        "dims=excluded.dims, vector_json=excluded.vector_json, "
        "text_hash=excluded.text_hash, updated_at=excluded.updated_at",
        (
            scope,
            ref_id,
            model,
            len(vec),
            json.dumps(vec),
            th,
            time.time(),
        ),
    )
    return True


def delete_embedding(conn: Any, *, scope: str, ref_id: str) -> None:
    conn.execute(
        "DELETE FROM memory_embeddings WHERE scope=? AND ref_id=?",
        (scope, ref_id),
    )


def load_embeddings(conn: Any, scope: str) -> list[tuple[str, list[float]]]:
    rows = conn.execute(
        "SELECT ref_id, vector_json FROM memory_embeddings WHERE scope=?",
        (scope,),
    ).fetchall()
    out: list[tuple[str, list[float]]] = []
    for row in rows:
        try:
            vec = json.loads(row["vector_json"] or "[]")
        except json.JSONDecodeError:
            continue
        if isinstance(vec, list) and vec:
            out.append((row["ref_id"], [float(x) for x in vec]))
    return out


def semantic_rank(
    conn: Any,
    *,
    scope: str,
    query: str,
    limit: int = 5,
) -> list[tuple[str, float]]:
    """Return ``(ref_id, score)`` pairs by cosine similarity."""
    qvec = embed_text(query)
    if not qvec:
        return []
    scored: list[tuple[str, float]] = []
    for ref_id, vec in load_embeddings(conn, scope):
        score = cosine(qvec, vec)
        if score > 0.15:
            scored.append((ref_id, score))
    scored.sort(key=lambda p: -p[1])
    return scored[: max(1, min(limit, 20))]


__all__ = [
    "DEFAULT_EMBED_MODEL",
    "embeddings_available",
    "embed_texts",
    "embed_text",
    "cosine",
    "text_hash",
    "upsert_embedding",
    "delete_embedding",
    "load_embeddings",
    "semantic_rank",
]
