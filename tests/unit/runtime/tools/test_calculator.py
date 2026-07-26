"""Safe arithmetic calculator backend tests.

The calculator uses an :mod:`ast` whitelist — no ``eval``/``exec`` — so the
safety tests below confirm that disallowed nodes (calls, attributes, names,
non-numeric literals) are rejected as error strings and never executed.
"""

from __future__ import annotations

from app.runtime.tools.calculator import evaluate, run


# --- happy path ---------------------------------------------------------


def test_simple_addition() -> None:
    assert evaluate("2+2") == "4"


def test_addition_with_spaces() -> None:
    assert evaluate("2 + 2") == "4"


def test_subtraction() -> None:
    assert evaluate("10 - 4") == "6"


def test_multiplication() -> None:
    assert evaluate("3 * 4") == "12"


def test_true_division() -> None:
    assert evaluate("10 / 4") == "2.5"


def test_floor_division() -> None:
    assert evaluate("10 // 4") == "2"


def test_modulo() -> None:
    assert evaluate("10 % 3") == "1"


def test_power() -> None:
    assert evaluate("2 ** 3") == "8"


def test_unary_negation() -> None:
    assert evaluate("-5") == "-5"


def test_unary_plus() -> None:
    assert evaluate("+5") == "5"


def test_operator_precedence() -> None:
    assert evaluate("2 + 3 * 4") == "14"


def test_parentheses() -> None:
    assert evaluate("(2 + 3) * 4") == "20"


def test_whole_float_rendered_as_int() -> None:
    assert evaluate("4 / 2") == "2"


def test_float_result() -> None:
    assert evaluate("1.5 + 2.5") == "4"


def test_nested_expression() -> None:
    assert evaluate("2 * (3 + 4) - 1") == "13"


# --- run() entry point --------------------------------------------------


def test_run_with_valid_args() -> None:
    assert run({"expression": "2 + 2"}) == "4"


def test_run_missing_expression() -> None:
    assert run({}).startswith("Error")


def test_run_non_string_expression() -> None:
    assert run({"expression": 123}).startswith("Error")


def test_run_non_dict_arguments() -> None:
    assert run("not a dict").startswith("Error")  # type: ignore[arg-type]


# --- invalid input returns error strings (never raises) -----------------


def test_empty_expression_is_error() -> None:
    assert evaluate("").startswith("Error")


def test_whitespace_only_is_error() -> None:
    assert evaluate("   ").startswith("Error")


def test_syntax_error_is_error_string() -> None:
    assert evaluate("2 +").startswith("Error")


def test_division_by_zero_is_error_string() -> None:
    assert evaluate("1 / 0").startswith("Error")


def test_floor_division_by_zero_is_error_string() -> None:
    assert evaluate("1 // 0").startswith("Error")


def test_modulo_by_zero_is_error_string() -> None:
    assert evaluate("1 % 0").startswith("Error")


# --- safety: disallowed AST nodes are rejected (no eval/exec) -----------


def test_name_node_rejected() -> None:
    assert evaluate("abc").startswith("Error")


def test_function_call_rejected() -> None:
    # __import__ would be catastrophic if eval'd; the AST whitelist blocks it.
    assert evaluate("__import__('os')").startswith("Error")


def test_open_call_rejected() -> None:
    assert evaluate("open('secret.txt')").startswith("Error")


def test_attribute_access_rejected() -> None:
    assert evaluate("(1).__class__").startswith("Error")


def test_string_literal_alone_rejected() -> None:
    # Only numeric literals are permitted.
    assert evaluate("'hello'").startswith("Error")


def test_boolean_literal_rejected() -> None:
    assert evaluate("True").startswith("Error")


def test_exponent_cap_prevents_huge_int() -> None:
    assert evaluate("2 ** 99999").startswith("Error")


def test_long_expression_rejected() -> None:
    assert evaluate("1 + " * 300 + "1").startswith("Error")
