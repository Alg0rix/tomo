"""Token Monitor package — Django-style module layout.

Exports ``module`` for :mod:`modules.registry` discovery.
"""

from __future__ import annotations

from .modules import META, module

__all__ = ["META", "module"]
