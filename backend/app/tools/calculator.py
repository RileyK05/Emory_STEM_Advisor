"""Small deterministic arithmetic tool; it never evaluates Python code."""

from __future__ import annotations

import ast
from decimal import Decimal, DecimalException, InvalidOperation, localcontext


class CalculationError(ValueError):
    """Expression is not in the bounded arithmetic language."""


_ALLOWED_BINARY = {
    ast.Add: lambda a, b: a + b,
    ast.Sub: lambda a, b: a - b,
    ast.Mult: lambda a, b: a * b,
    ast.Div: lambda a, b: a / b,
    ast.Pow: lambda a, b: a**b,
}
_ALLOWED_UNARY = {ast.UAdd: lambda a: a, ast.USub: lambda a: -a}


def calculate(expression: str) -> str:
    if not isinstance(expression, str) or not expression or len(expression) > 256:
        raise CalculationError("Expression must contain 1 to 256 characters.")
    try:
        tree = ast.parse(expression, mode="eval")
    except (SyntaxError, ValueError) as exc:
        raise CalculationError("Expression is not valid arithmetic.") from exc
    nodes = list(ast.walk(tree))
    if len(nodes) > 64:
        raise CalculationError("Expression has too many operations.")

    def evaluate(node: ast.AST) -> Decimal:
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
            literal = repr(node.value)
            if len(literal) > 48:
                raise CalculationError("Numeric literal is too large.")
            try:
                return Decimal(literal)
            except InvalidOperation as exc:
                raise CalculationError("Numeric literal is invalid.") from exc
        if isinstance(node, ast.UnaryOp) and type(node.op) in _ALLOWED_UNARY:
            return _ALLOWED_UNARY[type(node.op)](evaluate(node.operand))
        if isinstance(node, ast.BinOp) and type(node.op) in _ALLOWED_BINARY:
            left, right = evaluate(node.left), evaluate(node.right)
            if (
                isinstance(node.op, ast.Pow)
                and (right != right.to_integral_value() or abs(right) > 8)
            ):
                raise CalculationError("Exponent must be an integer from -8 to 8.")
            try:
                with localcontext() as ctx:
                    ctx.prec = 28
                    result = _ALLOWED_BINARY[type(node.op)](left, right)
            except (DecimalException, InvalidOperation, ZeroDivisionError, OverflowError) as exc:
                raise CalculationError("Arithmetic operation is undefined.") from exc
            if result and abs(result.adjusted()) > 50:
                raise CalculationError("Result magnitude exceeds the supported range.")
            return result
        raise CalculationError("Only numeric literals and +, -, *, /, and bounded powers are allowed.")

    try:
        result = evaluate(tree.body)
    except (InvalidOperation, ZeroDivisionError, OverflowError) as exc:
        raise CalculationError("Arithmetic operation is undefined.") from exc
    if not result.is_finite():
        raise CalculationError("Result must be finite.")
    rendered = format(result.normalize(), "f")
    if len(rendered) > 128:
        raise CalculationError("Result exceeds the supported precision.")
    return rendered
