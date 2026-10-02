"""Compatibility entry point for the application-owned durable job supervisor.

Runtime tool access goes through session-bound ``process`` controls. This module
retains the old test reset entry point; unbound global process access is removed.
"""
from app.services.background_jobs import manager


def reset() -> None:
    manager.reset()


__all__ = ['reset']
