import pytest

from app.tools.calculator import CalculationError, calculate


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("2 + 3 * 4", "14"),
        ("(100.5 - 0.5) / 4", "25"),
        ("-2 ** 3", "-8"),
        ("2 ** 8", "256"),
        ("3 ** -2", "0.1111111111111111111111111111"),
    ],
)
def test_calculator_returns_deterministic_decimal_results(expression, expected):
    assert calculate(expression) == expected


@pytest.mark.parametrize(
    "expression",
    [
        "__import__('os').system('whoami')",
        "open('/etc/passwd').read()",
        "x + 1",
        "True + 1",
        "2 ** 9",
        "1 / 0",
        "9" * 300,
    ],
)
def test_calculator_rejects_code_names_and_unbounded_operations(expression):
    with pytest.raises(CalculationError):
        calculate(expression)
