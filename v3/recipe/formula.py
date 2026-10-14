"""Evaluates the small formulas the model writes for a task's rules, so arithmetic and "which rule applies first" are done by code."""

import ast
import re
from decimal import Decimal, DivisionByZero, InvalidOperation
from typing import Any

_COMPARE = {
    ast.Lt: lambda a, b: a < b,
    ast.LtE: lambda a, b: a <= b,
    ast.Gt: lambda a, b: a > b,
    ast.GtE: lambda a, b: a >= b,
    ast.Eq: lambda a, b: a == b,
    ast.NotEq: lambda a, b: a != b,
}


# Tidies what a model tends to write into something Python can parse: symbols, grouped digits, currency signs, true/false words.
def _normalize(text: str) -> str:
    s = text.strip().strip("`")
    s = s.replace("≤", "<=").replace("≥", ">=").replace("≠", "!=").replace("×", "*").replace("÷", "/").replace("−", "-")
    s = re.sub(r"[₹$€£]", "", s)
    if not re.search(r"\b(abs|min|max)\s*\(", s, flags=re.I):  # inside a call a comma separates arguments: max(1,2)
        s = re.sub(r"(?<=\d),(?=\d)", "", s)  # 6,50,000 -> 650000
    s = re.sub(r"\{=\s*([^{}]*?)\s*\}", r"(\1)", s)  # a "{= 1+2}" the model wrote inside a formula is just a bracket
    s = s.replace("&&", " and ").replace("||", " or ")
    s = re.sub(r"\b(abs|min|max)\b(?=\s*\()", lambda m: m.group(1).lower(), s, flags=re.I)
    s = re.sub(r"(?<![<>=!])=(?!=)", "==", s)
    s = re.sub(r"\btrue\b", "True", s, flags=re.I)
    s = re.sub(r"\bfalse\b", "False", s, flags=re.I)
    s = re.sub(r"\b(and|or|not)\b", lambda m: m.group(1).lower(), s, flags=re.I)
    return s


# Evaluates a parsed formula node with exact decimal arithmetic. Raises ValueError for anything that is not allowed.
def _eval(node: ast.AST) -> Any:
    if isinstance(node, ast.Expression):
        return _eval(node.body)
    if isinstance(node, ast.Constant):
        v = node.value
        if isinstance(v, bool):
            return v
        if isinstance(v, (int, float)):
            return Decimal(repr(v))
        if isinstance(v, str):
            return v.strip().lower()
        raise ValueError("unsupported constant")
    if isinstance(node, ast.UnaryOp):
        v = _eval(node.operand)
        if isinstance(node.op, ast.Not):
            return not v
        if isinstance(node.op, ast.USub) and isinstance(v, Decimal):
            return -v
        if isinstance(node.op, ast.UAdd) and isinstance(v, Decimal):
            return v
        raise ValueError("unsupported unary operator")
    if isinstance(node, ast.BinOp):
        a, b = _eval(node.left), _eval(node.right)
        if not (isinstance(a, Decimal) and isinstance(b, Decimal)):
            raise ValueError("arithmetic needs numbers")
        if isinstance(node.op, ast.Add):
            return a + b
        if isinstance(node.op, ast.Sub):
            return a - b
        if isinstance(node.op, ast.Mult):
            return a * b
        if isinstance(node.op, ast.Div):
            return a / b
        raise ValueError("unsupported operator")
    if isinstance(node, ast.Call):
        # A few plain helpers on numbers (a difference "in either direction" is abs(a - b)); nothing else may be called
        if isinstance(node.func, ast.Name) and node.func.id in ("abs", "min", "max") and not node.keywords and 1 <= len(node.args) <= 6:
            args = [_eval(a) for a in node.args]
            if all(isinstance(a, Decimal) for a in args):
                if node.func.id == "abs" and len(args) == 1:
                    return abs(args[0])
                if node.func.id in ("min", "max") and len(args) >= 2:
                    return min(args) if node.func.id == "min" else max(args)
        raise ValueError("unsupported function")
    if isinstance(node, ast.BoolOp):
        values = [bool(_eval(v)) for v in node.values]
        return all(values) if isinstance(node.op, ast.And) else any(values)
    if isinstance(node, ast.Compare):
        left = _eval(node.left)
        for op, right_node in zip(node.ops, node.comparators):
            right = _eval(right_node)
            fn = _COMPARE.get(type(op))
            if fn is None or (type(left) is not type(right)):
                raise ValueError("cannot compare these")
            if not fn(left, right):
                return False
            left = right
        return True
    raise ValueError("unsupported formula")


# Evaluates a rule's formula. Returns True or False, or None when the formula cannot be read or computed
# (the caller then falls back to the model's own answer).
def evaluate(expr: str) -> bool | None:
    try:
        tree = ast.parse(_normalize(str(expr)), mode="eval")
        result = _eval(tree)
    except (SyntaxError, ValueError, InvalidOperation, DivisionByZero, RecursionError, TypeError):
        return None
    return result if isinstance(result, bool) else None


# Evaluates an arithmetic formula (numbers, + - * / and brackets) exactly and returns the result as plain text without a
# trailing ".0", or None when it cannot be read. Used for the numbers the model puts inside a value, like "received {=20+15+5}".
def number(expr: str) -> str | None:
    try:
        result = _eval(ast.parse(_normalize(str(expr)), mode="eval"))
    except (SyntaxError, ValueError, InvalidOperation, DivisionByZero, RecursionError, TypeError):
        return None
    if not isinstance(result, Decimal):
        return None
    text = format(result.normalize(), "f")
    return text


_INLINE = re.compile(r"\{+=\s*([^{}]*?)\s*\}+")  # doubled braces ({{=1+2}}) are read the same


# Replaces every {= formula } in a text with its computed number. Returns None when any formula cannot be computed,
# so the caller can refuse the answer instead of typing a half-computed value.
def render_inline(text: str) -> str | None:
    bad = False

    def one(match: re.Match[str]) -> str:
        nonlocal bad
        value = number(match.group(1))
        if value is None:
            bad = True
            return ""
        return value

    out = _INLINE.sub(one, str(text))
    return None if bad else out
