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
import math
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
# Cap on the number of decimal digits in an integer result. This is well
# under CPython's integer-to-string conversion limit (``sys.int_max_str_digits``
# defaults to 4300), so ``str(result)`` can never raise ``ValueError`` and
# nested powers such as ``(10**200)**200`` are refused before allocation.
_MAX_RESULT_DIGITS = 1000

_LOG10_2 = 0.3010299956639812


def _estimate_int_digits(value: int) -> int:
    """Approximate number of decimal digits in ``value`` (always >= 1).

    Uses ``bit_length() * log10(2)`` as a safe upper bound, so the estimate
    never under-counts and is cheap to compute for arbitrarily large ints.
    """
    if value == 0:
        return 1
    return max(1, int(value.bit_length() * _LOG10_2) + 1)


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
        if op_type is ast.Pow:
            # Cap exponents for both int and float operands — a float
            # exponent like 1e6 must not bypass the int-only guard.
            if abs(right) > _MAX_EXPONENT:
                raise ValueError(f"exponent too large (max {_MAX_EXPONENT})")
            # Estimate the integer result size before materialising it so a
            # nested power such as (10**200)**200 is refused before the huge
            # int is ever allocated.
            if (
                isinstance(left, int)
                and isinstance(right, int)
                and right > 0
                and left != 0
                and _estimate_int_digits(abs(left)) * right > _MAX_RESULT_DIGITS
            ):
                raise ValueError("result too large to represent")
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
        # Reject non-real results (e.g. (-2)**0.5 yields a complex number).
        if isinstance(result, complex):
            return "Error: result is not a real number"
        # Reject non-finite floats such as 2.0**2000 (overflow to inf) or NaN.
        if isinstance(result, float) and not math.isfinite(result):
            return "Error: result is too large to represent"
        # Refuse integers that exceed our digit cap before str() is attempted
        # (CPython itself refuses to stringify ints over ~4300 digits).
        if isinstance(result, int) and _estimate_int_digits(result) > _MAX_RESULT_DIGITS:
            return "Error: result too large to represent"
        return _format_result(result)
    except ZeroDivisionError as exc:
        return f"Error: {exc}"
    except ValueError as exc:
        return f"Error: {exc}"
    except RecursionError:
        return "Error: expression too complex"
    except Exception as exc:  # pragma: no cover - defensive catch-all
        return f"Error: could not evaluate expression: {exc}"


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
