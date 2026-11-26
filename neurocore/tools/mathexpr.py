"""
Avaliação segura de expressões matemáticas (sem eval/exec).

A expressão é convertida em árvore sintática (ast) e só são aceitos números,
operadores aritméticos, constantes conhecidas, a variável permitida e uma
lista fechada de funções. Há limites de tamanho para evitar contas que
travariam o servidor (ex.: 9**9**9).
"""

from __future__ import annotations

import ast
import math
import operator
import re
from fractions import Fraction
from typing import Any, Callable, Dict, Optional

MAX_EXPR_CHARS = 500
MAX_NODES = 300
MAX_POW_BITS = 1_000_000
MAX_FACTORIAL = 5000
MAX_COMB = 100_000

GOLDEN_RATIO = (1 + 5 ** 0.5) / 2


class ExpressionError(ValueError):
    pass


def normalize_expression(expr: str, variable: Optional[str] = None) -> str:
    """Aceita a notação usada em sala de aula: 2x², 3(x+1), x^2, 15% de 200, sen, tg, ×, ÷."""
    s = (expr or "").strip()
    s = re.sub(r"√\s*(\d+(?:\.\d+)?|[A-Za-z_]\w*)", r"sqrt(\1)", s)   # √2 -> sqrt(2), √x -> sqrt(x)
    replacements = {
        "×": "*", "·": "*", "⋅": "*", "÷": "/", "−": "-", "–": "-", "—": "-",
        "²": "**2", "³": "**3", "√": "sqrt", "π": "pi", "^": "**", "，": ",",
    }
    for src, dst in replacements.items():
        s = s.replace(src, dst)
    s = re.sub(r"(\d+(?:\.\d+)?)\s*%\s*(?:de|do|da|of)\s+", r"(\1/100)*", s, flags=re.IGNORECASE)
    s = re.sub(r"(\d+(?:\.\d+)?)\s*%(?!\s*[\w(])", r"(\1/100)", s)
    s = re.sub(r"(?<![A-Za-z_])arc(sin|sen|cos|tan|tg)\b", lambda m: "a" + {"sen": "sin", "tg": "tan"}.get(m.group(1), m.group(1)), s)
    s = re.sub(r"(?<![A-Za-z_])sen\b", "sin", s)
    s = re.sub(r"(?<![A-Za-z_])(?:tg|tang)\b", "tan", s)
    # Multiplicação implícita: 2x -> 2*x, 3(x+1) -> 3*(x+1), (a)(b) -> (a)*(b)
    s = re.sub(r"(?<![A-Za-z_\d.])(\d+(?:\.\d+)?)\s*(?=[A-Za-z_(])(?![eE][+-]?\d)", r"\1*", s)
    s = re.sub(r"\)\s*(?=[A-Za-z_\d(])", ")*", s)
    if variable:
        s = re.sub(rf"(?<![A-Za-z_]){re.escape(variable)}\s*(?=\()", f"{variable}*", s)
        s = re.sub(rf"(?<![A-Za-z_]){re.escape(variable)}(?=\d)", f"{variable}*", s)
    return s


def _check_tree(expr: str) -> ast.Expression:
    if not expr or not expr.strip():
        raise ExpressionError("Expressão vazia.")
    if len(expr) > MAX_EXPR_CHARS:
        raise ExpressionError(f"Expressão longa demais (máximo {MAX_EXPR_CHARS} caracteres).")
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError:
        raise ExpressionError("Expressão inválida. Use a notação do Python: *, /, ** e parênteses.")
    if sum(1 for _ in ast.walk(tree)) > MAX_NODES:
        raise ExpressionError("Expressão complexa demais.")
    return tree


# ---------------------------------------------------------------------------
# Modo escalar (calculadora)
# ---------------------------------------------------------------------------

def _safe_pow(base: Any, exp: Any) -> Any:
    if isinstance(base, int) and isinstance(exp, int) and exp > 0:
        if abs(base) > 1 and base.bit_length() * exp > MAX_POW_BITS:
            raise ExpressionError("Resultado grande demais para calcular.")
        return base ** exp
    try:
        result = float(base) ** float(exp) if not (isinstance(base, int) and isinstance(exp, int)) else base ** exp
    except OverflowError:
        raise ExpressionError("Resultado grande demais para calcular.")
    if isinstance(result, complex):
        raise ExpressionError("O resultado não é um número real (ex.: raiz par de número negativo).")
    return result


def _int_arg(value: Any, name: str, limit: int) -> int:
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if not isinstance(value, int) or value < 0:
        raise ExpressionError(f"{name} precisa de inteiros não negativos.")
    if value > limit:
        raise ExpressionError(f"Valor grande demais para {name} (máximo {limit}).")
    return value


def _factorial(n: Any) -> int:
    return math.factorial(_int_arg(n, "factorial", MAX_FACTORIAL))


def _comb(n: Any, k: Any) -> int:
    return math.comb(_int_arg(n, "comb", MAX_COMB), _int_arg(k, "comb", MAX_COMB))


def _perm(n: Any, k: Any = None) -> int:
    n = _int_arg(n, "perm", MAX_COMB)
    return math.perm(n, None if k is None else _int_arg(k, "perm", MAX_COMB))


def _log(x: Any, base: Any = 10) -> float:
    return math.log(x, base)


def _root(x: Any, n: Any) -> float:
    if n == 0:
        raise ExpressionError("Índice da raiz não pode ser zero.")
    if x < 0 and float(n).is_integer() and int(n) % 2 == 1:
        return -((-x) ** (1 / n))
    return x ** (1 / n)


def _scalar_functions(degrees: bool) -> Dict[str, Callable[..., Any]]:
    to_rad = math.radians if degrees else (lambda v: v)
    from_rad = math.degrees if degrees else (lambda v: v)
    return {
        "sqrt": math.sqrt, "cbrt": lambda x: math.copysign(abs(x) ** (1 / 3), x), "root": _root,
        "exp": math.exp, "ln": math.log, "log": _log, "log10": math.log10, "log2": math.log2,
        "sin": lambda x: math.sin(to_rad(x)), "cos": lambda x: math.cos(to_rad(x)),
        "tan": lambda x: math.tan(to_rad(x)),
        "asin": lambda x: from_rad(math.asin(x)), "acos": lambda x: from_rad(math.acos(x)),
        "atan": lambda x: from_rad(math.atan(x)),
        "sinh": math.sinh, "cosh": math.cosh, "tanh": math.tanh,
        "abs": abs, "floor": math.floor, "ceil": math.ceil, "round": round, "trunc": math.trunc,
        "factorial": _factorial, "fact": _factorial, "comb": _comb, "perm": _perm,
        "gcd": math.gcd, "mdc": math.gcd, "lcm": math.lcm, "mmc": math.lcm,
        "degrees": math.degrees, "radians": math.radians, "hypot": math.hypot,
        "min": min, "max": max,
    }


CONSTANTS = {"pi": math.pi, "e": math.e, "tau": math.tau, "phi": GOLDEN_RATIO}

_BIN_OPS = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
    ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod,
}


def evaluate(expression: str, degrees: bool = False, variables: Optional[Dict[str, float]] = None) -> Any:
    """Calcula a expressão e devolve int ou float."""
    expr = normalize_expression(expression)
    tree = _check_tree(expr)
    funcs = _scalar_functions(degrees)
    names = dict(CONSTANTS)
    names.update(variables or {})

    def visit(node: ast.AST) -> Any:
        if isinstance(node, ast.Expression):
            return visit(node.body)
        if isinstance(node, ast.Constant) and type(node.value) in (int, float):
            return node.value
        if isinstance(node, ast.Name):
            if node.id in names:
                return names[node.id]
            raise ExpressionError(f"Nome desconhecido: {node.id}")
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            value = visit(node.operand)
            return value if isinstance(node.op, ast.UAdd) else -value
        if isinstance(node, ast.BinOp):
            left, right = visit(node.left), visit(node.right)
            if isinstance(node.op, ast.Pow):
                return _safe_pow(left, right)
            op = _BIN_OPS.get(type(node.op))
            if op is None:
                raise ExpressionError("Operador não permitido.")
            for value in (left, right):
                if isinstance(value, int) and value.bit_length() > MAX_POW_BITS:
                    raise ExpressionError("Número grande demais.")
            return op(left, right)
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in funcs or node.keywords:
                name = getattr(node.func, "id", "?")
                raise ExpressionError(f"Função não permitida: {name}")
            args = [visit(arg) for arg in node.args]
            if not args:
                raise ExpressionError(f"A função {node.func.id} precisa de argumentos.")
            return funcs[node.func.id](*args)
        raise ExpressionError("Expressão não permitida (use apenas números, operadores e funções matemáticas).")

    try:
        result = visit(tree)
    except ExpressionError:
        raise
    except ZeroDivisionError:
        raise ExpressionError("Divisão por zero.")
    except OverflowError:
        raise ExpressionError("Resultado grande demais para calcular.")
    except ValueError as exc:
        if "domain" in str(exc):
            raise ExpressionError("Operação fora do domínio (ex.: raiz de número negativo ou logaritmo de número menor ou igual a zero).")
        raise ExpressionError("Operação matemática inválida.")
    except TypeError:
        raise ExpressionError("Número de argumentos inválido para uma função.")
    if isinstance(result, float) and (math.isnan(result) or math.isinf(result)):
        raise ExpressionError("O resultado não é um número finito.")
    if isinstance(result, bool) or not isinstance(result, (int, float)):
        raise ExpressionError("Resultado inválido.")
    return result


def format_number(value: Any, decimals: int = 12) -> str:
    """Formato padrão (ponto decimal), sem ruído de ponto flutuante."""
    if isinstance(value, int):
        return str(value) if abs(value) < 10 ** 40 else f"{value:.10e}"
    if float(value).is_integer() and abs(value) < 1e15:
        return str(int(value))
    return f"{value:.{decimals}g}"


def format_number_br(value: Any) -> str:
    """Formato brasileiro: 1.234.567,89."""
    if isinstance(value, int) or (isinstance(value, float) and value.is_integer() and abs(value) < 1e15):
        return f"{int(value):,}".replace(",", ".")
    text = format_number(value)
    if "e" in text:
        return text.replace(".", ",")
    integer, _, frac = text.partition(".")
    sign = "-" if integer.startswith("-") else ""
    integer = integer.lstrip("-")
    grouped = f"{int(integer):,}".replace(",", ".") if integer else "0"
    return f"{sign}{grouped},{frac}" if frac else f"{sign}{grouped}"


def as_fraction(value: Any) -> Optional[str]:
    """Fração simples equivalente (ex.: 0.333... -> 1/3), quando existir."""
    if isinstance(value, int) or float(value).is_integer():
        return None
    frac = Fraction(value).limit_denominator(1000)
    if frac.denominator == 1 or frac.denominator > 100 or abs(frac.numerator) > 100000:
        return None
    if abs(float(frac) - value) > 1e-12 * max(1.0, abs(value)):
        return None
    return f"{frac.numerator}/{frac.denominator}"


# ---------------------------------------------------------------------------
# Modo vetorial (gráficos de funções)
# ---------------------------------------------------------------------------

def compile_function(expression: str, variable: str = "x") -> Callable[[Any], Any]:
    """Compila f(x) para uma função que recebe um array numpy de x."""
    import numpy as np

    expr = normalize_expression(expression, variable=variable)
    # "f(x) = ..." ou "y = ..." -> só o lado direito
    if "=" in expr:
        expr = expr.split("=", 1)[1]
    tree = _check_tree(expr.strip())

    def log_any(x: Any, base: Any = 10) -> Any:
        return np.log(x) / np.log(base)

    vector_funcs: Dict[str, Callable[..., Any]] = {
        "sqrt": np.sqrt, "cbrt": np.cbrt, "exp": np.exp, "ln": np.log, "log": log_any,
        "log10": np.log10, "log2": np.log2, "sin": np.sin, "cos": np.cos, "tan": np.tan,
        "asin": np.arcsin, "acos": np.arccos, "atan": np.arctan, "sinh": np.sinh,
        "cosh": np.cosh, "tanh": np.tanh, "abs": np.abs, "floor": np.floor, "ceil": np.ceil,
        "round": np.round, "min": np.minimum, "max": np.maximum,
        "root": lambda x, n: np.sign(x) * np.abs(x) ** (1.0 / n) if float(n) % 2 == 1 else np.power(x, 1.0 / n),
    }
    vec_ops = {
        ast.Add: np.add, ast.Sub: np.subtract, ast.Mult: np.multiply, ast.Div: np.divide,
        ast.FloorDiv: np.floor_divide, ast.Mod: np.mod, ast.Pow: np.power,
    }

    def build(node: ast.AST) -> Callable[[Any], Any]:
        if isinstance(node, ast.Expression):
            return build(node.body)
        if isinstance(node, ast.Constant) and type(node.value) in (int, float):
            value = float(node.value)
            return lambda x: value
        if isinstance(node, ast.Name):
            if node.id == variable:
                return lambda x: x
            if node.id in CONSTANTS:
                value = CONSTANTS[node.id]
                return lambda x: value
            raise ExpressionError(f"Nome desconhecido: {node.id} (use {variable} como variável)")
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            inner = build(node.operand)
            return inner if isinstance(node.op, ast.UAdd) else (lambda x: np.negative(inner(x)))
        if isinstance(node, ast.BinOp) and type(node.op) in vec_ops:
            left, right, op = build(node.left), build(node.right), vec_ops[type(node.op)]
            if isinstance(node.op, ast.Pow):
                return lambda x: np.power(np.asarray(left(x), dtype=float), right(x))
            return lambda x: op(left(x), right(x))
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in vector_funcs or node.keywords or not node.args:
                raise ExpressionError(f"Função não permitida em gráficos: {getattr(node.func, 'id', '?')}")
            func = vector_funcs[node.func.id]
            args = [build(arg) for arg in node.args]
            return lambda x: func(*[a(x) for a in args])
        raise ExpressionError("Expressão não permitida.")

    compiled = build(tree)

    def evaluate_array(xs: Any) -> Any:
        with np.errstate(all="ignore"):
            result = np.asarray(compiled(xs), dtype=float)
        if result.shape != np.shape(xs):
            result = np.broadcast_to(result, np.shape(xs)).astype(float)
        return result

    return evaluate_array
