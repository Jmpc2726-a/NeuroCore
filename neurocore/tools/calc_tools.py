"""
Calculadora exata e segura para o agente (contas, porcentagens, trigonometria,
logaritmos e combinatória), sem usar eval.
"""

from __future__ import annotations

from typing import Any

from neurocore.tools.base import BaseTool, ToolResult
from neurocore.tools.mathexpr import (
    ExpressionError,
    as_fraction,
    evaluate,
    format_number,
    format_number_br,
    normalize_expression,
)


class CalculatorTool(BaseTool):
    name = "calculator"
    description = (
        "Calcula expressões matemáticas com precisão. Use para qualquer conta não trivial "
        "(porcentagens, juros, potências, raízes, logaritmos, trigonometria, combinatória). "
        "Sintaxe: + - * / ** (ou ^), parênteses, porcentagem (ex.: '15% de 200'), multiplicação "
        "implícita entre número e parênteses ou função (ex.: '2(3+4)', '3pi'). Funções: sqrt, cbrt, root(x, n), exp, ln, log (base 10), "
        "log(x, b), log2, sin/sen, cos, tan/tg, asin, acos, atan, abs, floor, ceil, round, factorial, "
        "comb(n, k), perm(n, k), mdc/gcd, mmc/lcm, hypot. Constantes: pi, e. Use ponto como separador decimal."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "expression": {
                "type": "string",
                "description": "Expressão a calcular, ex.: '(1 + 0.05)**12 * 1500' ou 'comb(10, 3) / comb(15, 3)'.",
            },
            "angle_unit": {
                "type": "string",
                "enum": ["radianos", "graus"],
                "description": "Unidade dos ângulos nas funções trigonométricas. Padrão: radianos.",
            },
        },
        "required": ["expression"],
    }

    def _run_sync(self, tool_call_id: str = "", **kwargs: Any) -> ToolResult:
        expression = str(kwargs.get("expression") or "").strip()
        degrees = str(kwargs.get("angle_unit") or "").lower().startswith("gra")
        try:
            value = evaluate(expression, degrees=degrees)
        except ExpressionError as exc:
            return ToolResult(tool_call_id=tool_call_id, name=self.name, status="error", error=str(exc))

        output = {
            "expression": expression,
            "interpreted_as": normalize_expression(expression),
            "result": format_number(value),
            "result_br": format_number_br(value),
        }
        fraction = as_fraction(value)
        if fraction:
            output["fraction"] = fraction
        if degrees:
            output["angle_unit"] = "graus"
        return ToolResult(tool_call_id=tool_call_id, name=self.name, status="success", output=output)
