"""Conversation memory and knowledge retrieval.

Layers (see README Learning section):
* Knowledge base — ``recall`` / ``remember`` (FTS + optional embeddings)
* Conversation memory — session history + rolled ``session_summaries``
* Artifacts — ``save_artifact`` catalog
* Skills — ``manage_skill`` / ``use_skill``
* Agent state — ``agent_state`` key/value store

Hybrid retrieval: :mod:`app.runtime.memory.retrieve`.
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
