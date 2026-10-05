"""Shared execution-ceiling revalidation never accepts a missing context.

Guard rail for host helpers and durable job observation that call
``access.revalidate(current_execution())``: an unbound execution context must
fail closed with a policy denial, not an unhandled ``AttributeError``.
"""
from __future__ import annotations

import pytest

from app.runtime.access import AccessDenied
from app.services.access import access


def test_revalidate_none_is_a_clean_denial():
    with pytest.raises(AccessDenied):
        access.revalidate(None)
