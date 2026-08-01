"""Conversation memory and knowledge retrieval.

Layers (see README Learning section):
* Curated memory — ``memory`` tool → ``USER.md`` / ``MEMORY.md`` (frozen per session)
* Knowledge base — ``recall`` / ``remember`` (FTS5; embeddings optional)
* Conversation memory — session history + rolled ``session_summaries`` + FTS search
* Artifacts — ``save_artifact`` catalog
* Skills — ``manage_skill`` / ``use_skill``
* Agent state — ``agent_state`` key/value store

Turn reuse: :mod:`app.runtime.memory.retrieve` + curated ``prompt_block``.
"""

from __future__ import annotations

from app.runtime.memory.retrieve import retrieve_for_turn, search_knowledge_hybrid
from app.runtime.memory.store import remember, search

__all__ = [
    "search",
    "remember",
    "retrieve_for_turn",
    "search_knowledge_hybrid",
]
