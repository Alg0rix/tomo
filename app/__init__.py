"""Tomo — 友達 (tomodachi), a general-purpose agent swarm.

Alpha kitchen-sink: FastAPI UI + SQLite store, coordinator loop with
delegation, tools/workplaces, KB recall, Telegram, and interval scheduler.
Eval UI stays gated behind ``TOMO_EVAL_UI``.
"""

try:
    from importlib.metadata import version as _pkg_version

    __version__ = _pkg_version("tomo")
except Exception:
    __version__ = "0.0.0"
