"""Safe arithmetic calculator tool backend.

Evaluates a math expression string using Python's :mod:`ast` with a strict
whitelist of allowed nodes — numeric literals and arithmetic operators only.
No ``eval``/``exec`` of arbitrary code is ever performed, so expressions
like ``__import__("os")`` or ``open("file")`` are rejected at the AST level
before any code could run.

The public entry point is :func:`run`, which accepts the already-parsed
tool arguments dict (``{"expression": "2 + 2"}``) and returns the result as
a string. Invalid input yields a human-readable ``"Error: ..."`` string —
the caller (registry / agent loop) never sees an exception.
"""

from __future__ import annotations

import ast
import operator
from typing import Any

# Whitelisted binary operators (literals + arithmetic only).
_BINARY_OPS: dict[type, Any] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}

# Whitelisted unary operators (+x, -x).
_UNARY_OPS: dict[type, Any] = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}

# Defensive caps to keep evaluation cheap and deterministic.
_MAX_EXPR_LEN = 512
_MAX_EXPONENT = 1000


def _format_result(value: int | float) -> str:
    """Render a numeric result as a clean string.

    Whole-valued floats (e.g. ``4.0``) are shown without a trailing ``.0``
    so ``4 / 2`` reads as ``"4"`` rather than ``"4.0"``.
    """
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _eval_node(node: ast.AST) -> int | float:
    """Recursively evaluate an AST node against the safe whitelist.

    Raises :class:`ValueError` for any disallowed node/operator and
    :class:`ZeroDivisionError` for division/modulo by zero. These are
    caught by :func:`evaluate` and turned into error strings.
    """
    if isinstance(node, ast.Expression):
        return _eval_node(node.body)

    if isinstance(node, ast.Constant):
        # Numeric literals only; bool is a subclass of int, so exclude it.
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            raise ValueError("only numeric literals are allowed")
        return node.value

    if isinstance(node, ast.BinOp):
        op_type = type(node.op)
        if op_type not in _BINARY_OPS:
            raise ValueError(f"unsupported operator: {op_type.__name__}")
        left = _eval_node(node.left)
        right = _eval_node(node.right)
        if op_type in (ast.Div, ast.FloorDiv, ast.Mod) and right == 0:
            raise ZeroDivisionError("division by zero")
        if op_type is ast.Pow and isinstance(right, int) and abs(right) > _MAX_EXPONENT:
            raise ValueError(f"exponent too large (max {_MAX_EXPONENT})")
        return _BINARY_OPS[op_type](left, right)

    if isinstance(node, ast.UnaryOp):
        op_type = type(node.op)
        if op_type not in _UNARY_OPS:
            raise ValueError(f"unsupported operator: {op_type.__name__}")
        return _UNARY_OPS[op_type](_eval_node(node.operand))

    raise ValueError(f"disallowed expression element: {type(node).__name__}")


def evaluate(expression: str) -> str:
    """Safely evaluate an arithmetic expression string.

    Returns the result as a string on success or an ``"Error: ..."`` string
    on failure. This function never raises.
    """
    if not isinstance(expression, str):
        return "Error: expression must be a string"
    text = expression.strip()
    if not text:
        return "Error: expression must not be empty"
    if len(text) > _MAX_EXPR_LEN:
        return f"Error: expression too long (max {_MAX_EXPR_LEN} characters)"

    try:
        tree = ast.parse(text, mode="eval")
    except SyntaxError as exc:
        return f"Error: invalid expression: {exc.msg}"

    try:
        result = _eval_node(tree)
    except ZeroDivisionError as exc:
        return f"Error: {exc}"
    except ValueError as exc:
        return f"Error: {exc}"
    except RecursionError:
        return "Error: expression too complex"
    except Exception as exc:  # pragma: no cover - defensive catch-all
        return f"Error: could not evaluate expression: {exc}"

    return _format_result(result)


def run(arguments: dict[str, Any]) -> str:
    """Tool backend entry point used by the registry.

    Accepts the OpenAI tool arguments dict and returns the result string.
    A missing or non-string ``expression`` yields an error string.
    """
    if not isinstance(arguments, dict):
        return "Error: 'expression' argument must be a string"
    expression = arguments.get("expression")
    if not isinstance(expression, str):
        return "Error: 'expression' argument must be a string"
    return evaluate(expression)


__all__ = ["run", "evaluate"]
