"""
Ferramenta de gráficos do NeuroCore (matplotlib, sem pyplot).

Desenha funções matemáticas e gráficos de dados num PNG com o tema escuro da
interface e devolve, junto com a imagem, uma análise numérica (raízes,
extremos, interseções, estatísticas) para o modelo comentar com precisão.
"""

from __future__ import annotations

import asyncio
import io
import math
import re
import unicodedata
from typing import Any, Dict, List, Optional, Sequence, Tuple

from neurocore.attachments import AttachmentError, save_image_bytes
from neurocore.tools.base import BaseTool, ToolResult
from neurocore.tools.context import ToolContext
from neurocore.tools.mathexpr import ExpressionError, compile_function, format_number_br

PALETTE = ["#34d399", "#60a5fa", "#fbbf24", "#f87171", "#a78bfa", "#f472b6", "#2dd4bf", "#fb923c"]
BG = "#111714"
FG = "#e4ece7"
MUTED = "#a6b6ad"
GRID = "#25302a"

MAX_SERIES = 8
MAX_VALUES = 2000
MAX_FUNCTIONS = 6
MAX_CATEGORIES = 100
CHART_TYPES = ("function", "line", "bar", "scatter", "pie", "histogram")


class ChartError(ValueError):
    pass


def _fmt(value: float) -> str:
    rounded = round(float(value), 4)
    if rounded == 0:
        rounded = 0.0
    return format_number_br(int(rounded) if float(rounded).is_integer() else rounded)


def _number(value: Any) -> float:
    if isinstance(value, bool):
        raise ChartError("Valores devem ser números.")
    if isinstance(value, (int, float)):
        result = float(value)
    elif isinstance(value, str):
        try:
            result = float(value.strip().replace(",", "."))
        except ValueError:
            raise ChartError(f"Valor não numérico: {value!r}")
    else:
        raise ChartError("Valores devem ser números.")
    if not math.isfinite(result):
        raise ChartError("Valores devem ser finitos.")
    return result


def _numbers(values: Any, what: str) -> List[float]:
    if not isinstance(values, (list, tuple)) or not values:
        raise ChartError(f"{what}: informe uma lista de números.")
    if len(values) > MAX_VALUES:
        raise ChartError(f"{what}: no máximo {MAX_VALUES} valores.")
    return [_number(v) for v in values]


def _escape_mathtext(text: str) -> str:
    return text.replace("$", r"\$")


def _slug(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return text[:40] or "grafico"


# ---------------------------------------------------------------------------
# Análise numérica de funções
# ---------------------------------------------------------------------------

def _clean_discontinuities(xs: Any, ys: Any) -> Tuple[Any, List[float]]:
    """Troca saltos e valores infinitos por NaN e devolve onde há assíntotas prováveis."""
    import numpy as np

    raw_finite = np.isfinite(ys)
    ys = np.where(raw_finite, ys, np.nan)
    finite = ys[np.isfinite(ys)]
    if finite.size < 3:
        return ys, []
    lo, hi = np.percentile(finite, [2, 98])
    span = max(hi - lo, 1e-9)
    jumps = np.where(np.abs(np.diff(ys)) > 4 * span)[0]
    ys = ys.copy()
    # Assíntotas (ex.: 1/x, tan x): não liga os pontos através do salto.
    ys[jumps] = np.nan
    breaks = [float((xs[i] + xs[i + 1]) / 2) for i in jumps]
    # Pontos isolados fora do domínio no meio do intervalo (ex.: 1/x em x = 0).
    for i in np.where(~raw_finite)[0]:
        if 0 < i < len(ys) - 1 and raw_finite[i - 1] and raw_finite[i + 1]:
            breaks.append(float(xs[i]))
    breaks.sort()
    clusters: List[List[float]] = []
    step = float(xs[1] - xs[0])
    for b in breaks:
        if clusters and abs(b - clusters[-1][-1]) <= 3 * step:
            clusters[-1].append(b)
        else:
            clusters.append([b])
    return ys, [(c[0] + c[-1]) / 2 for c in clusters]


def _sign_changes(xs: Any, ys: Any) -> List[float]:
    import numpy as np

    roots: List[float] = []
    exact = np.where(ys == 0)[0]
    for i in exact:
        roots.append(float(xs[i]))
    a, b = ys[:-1], ys[1:]
    mask = np.isfinite(a) & np.isfinite(b) & (a * b < 0)
    for i in np.where(mask)[0]:
        x0, x1, y0, y1 = xs[i], xs[i + 1], a[i], b[i]
        roots.append(float(x0 - y0 * (x1 - x0) / (y1 - y0)))
    roots.sort()
    merged: List[float] = []
    step = float(xs[1] - xs[0]) if len(xs) > 1 else 1e-9
    for r in roots:
        if not merged or abs(r - merged[-1]) > 2 * step:
            merged.append(r)
    return merged


def _local_extrema(xs: Any, ys: Any) -> List[Tuple[str, float, float]]:
    import numpy as np

    out: List[Tuple[str, float, float]] = []
    d = np.diff(ys)
    for i in range(1, len(d)):
        if not (np.isfinite(d[i - 1]) and np.isfinite(d[i])):
            continue
        if d[i - 1] > 0 and d[i] < 0:
            kind = "máximo local"
        elif d[i - 1] < 0 and d[i] > 0:
            kind = "mínimo local"
        else:
            continue
        # Refinamento parabólico pelos três pontos vizinhos (exato para parábolas).
        y0, y1, y2 = float(ys[i - 1]), float(ys[i]), float(ys[i + 1])
        h = float(xs[i + 1] - xs[i])
        denom = y0 - 2 * y1 + y2
        x_best, y_best = float(xs[i]), y1
        if denom != 0 and h > 0:
            p = 0.5 * (y0 - y2) / denom
            if abs(p) <= 1:
                x_best = float(xs[i]) + p * h
                y_best = y1 - 0.25 * (y0 - y2) * p
        out.append((kind, x_best, y_best))
        if len(out) >= 4:
            break
    return out


def _sample_points(x_min: float, x_max: float) -> Any:
    """Pontos igualmente espaçados alinhados a múltiplos do passo (inclui 0 e inteiros)."""
    import numpy as np

    raw = (x_max - x_min) / 2000
    magnitude = 10 ** math.floor(math.log10(raw))
    step = next(m * magnitude for m in (1, 2, 2.5, 5, 10) if raw <= m * magnitude)
    k0, k1 = math.ceil(x_min / step), math.floor(x_max / step)
    xs = np.arange(k0, k1 + 1, dtype=float) * step
    if xs.size == 0 or xs[0] > x_min:
        xs = np.concatenate(([x_min], xs))
    if xs[-1] < x_max:
        xs = np.concatenate((xs, [x_max]))
    return xs


def _analyze_function(
    label: str,
    xs: Any,
    ys: Any,
    breaks: Optional[List[float]] = None,
    y_at_zero: Optional[float] = None,
) -> List[str]:
    import numpy as np

    notes: List[str] = []
    finite = np.isfinite(ys)
    if not finite.any():
        return [f"{label}: não está definida (valores reais) no intervalo mostrado."]
    defined = np.where(finite)[0]
    if defined[0] > 0 or defined[-1] < len(xs) - 1:
        if len(defined) == defined[-1] - defined[0] + 1:
            notes.append(
                f"{label}: só está definida (valores reais) para x entre ≈ {_fmt(xs[defined[0]])} "
                f"e ≈ {_fmt(xs[defined[-1]])} no intervalo mostrado."
            )
    roots = _sign_changes(xs, ys)
    if roots:
        shown = ", ".join(f"x ≈ {_fmt(r)}" for r in roots[:6])
        extra = f" (e mais {len(roots) - 6})" if len(roots) > 6 else ""
        notes.append(f"{label}: zeros em {shown}{extra}.")
    else:
        notes.append(f"{label}: não cruza o eixo x no intervalo mostrado.")
    if xs[0] <= 0 <= xs[-1] and y_at_zero is not None and math.isfinite(y_at_zero):
        notes.append(f"{label}: corta o eixo y em y ≈ {_fmt(y_at_zero)}.")
    if breaks:
        shown = ", ".join(f"x ≈ {_fmt(b)}" for b in breaks[:6])
        notes.append(f"{label}: possível assíntota vertical (descontinuidade) perto de {shown}.")
    extrema = [] if breaks else _local_extrema(xs, ys)
    for kind, x, y in extrema:
        notes.append(f"{label}: {kind} em ({_fmt(x)}; {_fmt(y)}).")
    if not extrema and not breaks:
        fy = ys[finite]
        fx = xs[finite]
        notes.append(
            f"{label}: no intervalo, vai de y ≈ {_fmt(fy.min())} (em x ≈ {_fmt(fx[fy.argmin()])}) "
            f"a y ≈ {_fmt(fy.max())} (em x ≈ {_fmt(fx[fy.argmax()])})."
        )
    return notes


# ---------------------------------------------------------------------------
# Desenho
# ---------------------------------------------------------------------------

def _new_figure():
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    fig = Figure(figsize=(8, 5), dpi=150, facecolor=BG)
    FigureCanvasAgg(fig)
    ax = fig.add_subplot(111)
    ax.set_facecolor(BG)
    for spine in ax.spines.values():
        spine.set_color(GRID)
    ax.tick_params(colors=MUTED, labelsize=9)
    ax.grid(True, color=GRID, linewidth=0.8, alpha=0.9)
    ax.set_axisbelow(True)
    return fig, ax


def _finish(fig, ax, spec: Dict[str, Any], escape: bool, legend: bool) -> bytes:
    def t(value: Optional[str]) -> str:
        text = str(value or "")[:120]
        return _escape_mathtext(text) if escape else text

    if spec.get("title"):
        ax.set_title(t(spec["title"]), color=FG, fontsize=13, pad=12, fontweight="bold")
    if spec.get("x_label"):
        ax.set_xlabel(t(spec["x_label"]), color=MUTED, fontsize=10)
    if spec.get("y_label"):
        ax.set_ylabel(t(spec["y_label"]), color=MUTED, fontsize=10)
    if legend:
        handles, labels = ax.get_legend_handles_labels()
        if labels:
            leg = ax.legend(facecolor=BG, edgecolor=GRID, fontsize=9, labelcolor=FG)
            leg.get_frame().set_alpha(0.9)
    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor=BG, bbox_inches="tight", pad_inches=0.25)
    return buf.getvalue()


def _auto_domain(compiled_functions: List[Any]) -> Tuple[float, float]:
    """Escolhe um intervalo de x que mostre raízes, extremos e interseções."""
    import numpy as np

    xs = np.linspace(-20, 20, 4001)
    features: List[float] = []
    curves = []
    for func in compiled_functions:
        ys, breaks = _clean_discontinuities(xs, func(xs))
        curves.append(ys)
        features.extend(_sign_changes(xs, ys))
        features.extend(x for _, x, _ in _local_extrema(xs, ys))
        features.extend(breaks)
        i0 = int(np.argmin(np.abs(xs)))
        if np.isfinite(ys[i0]):
            features.append(0.0)
        finite = np.where(np.isfinite(ys))[0]
        if finite.size and (finite[0] > 0 or finite[-1] < len(xs) - 1):
            features.append(float(xs[finite[0]]))  # início do domínio (ex.: sqrt, log)
    for i in range(len(curves)):
        for j in range(i + 1, len(curves)):
            features.extend(_sign_changes(xs, curves[i] - curves[j]))
    features = [f for f in features if -20 <= f <= 20]
    if not features:
        return -10.0, 10.0
    lo, hi = min(features), max(features)
    span = hi - lo
    if span > 24:  # funções periódicas ou com muitos pontos notáveis
        return -10.0, 10.0
    pad = max(1.0, 0.3 * span)
    lo, hi = lo - pad, hi + pad
    if hi - lo < 6:
        center = (lo + hi) / 2
        lo, hi = center - 3, center + 3
    return float(math.floor(lo)), float(math.ceil(hi))


def _draw_functions(spec: Dict[str, Any], escape: bool) -> Tuple[bytes, List[str]]:
    import numpy as np

    functions = spec.get("functions") or []
    if not isinstance(functions, list) or not functions:
        raise ChartError("Informe ao menos uma função em 'functions' (ex.: {\"expression\": \"x**2 - 4\"}).")
    if len(functions) > MAX_FUNCTIONS:
        raise ChartError(f"No máximo {MAX_FUNCTIONS} funções por gráfico.")
    parsed: List[Tuple[Dict[str, Any], Any]] = []
    for item in functions:
        if isinstance(item, str):
            item = {"expression": item}
        if not isinstance(item, dict) or not str(item.get("expression") or "").strip():
            raise ChartError("Cada função precisa de 'expression'.")
        expr = str(item["expression"]).strip()
        try:
            parsed.append((item, compile_function(expr)))
        except ExpressionError as exc:
            raise ChartError(f"Expressão '{expr}': {exc}")

    if spec.get("x_min") is None and spec.get("x_max") is None:
        x_min, x_max = _auto_domain([f for _, f in parsed])
    else:
        x_min = _number(spec["x_min"]) if spec.get("x_min") is not None else -10.0
        x_max = _number(spec["x_max"]) if spec.get("x_max") is not None else 10.0
    if not x_min < x_max or x_max - x_min > 1e7:
        raise ChartError("Intervalo inválido: x_min precisa ser menor que x_max.")

    xs = _sample_points(x_min, x_max)
    fig, ax = _new_figure()
    analysis: List[str] = []
    curves: List[Tuple[str, Any]] = []
    all_finite: List[Any] = []
    for index, (item, compiled) in enumerate(parsed):
        expr = str(item["expression"]).strip()
        ys, breaks = _clean_discontinuities(xs, compiled(xs))
        label = str(item.get("label") or "").strip() or f"f{index + 1 if len(parsed) > 1 else ''}(x) = {expr}"
        short = label.split("=")[0].strip() if "=" in label else label
        color = PALETTE[index % len(PALETTE)]
        ax.plot(xs, ys, color=color, linewidth=2.2, label=_escape_mathtext(label) if escape else label)
        curves.append((short, ys))
        finite = ys[np.isfinite(ys)]
        if finite.size:
            all_finite.append(finite)
        roots = _sign_changes(xs, ys)
        if roots:
            ax.scatter(roots[:12], [0] * len(roots[:12]), color=color, s=28, zorder=5, edgecolors=BG, linewidths=1.2)
        y0 = float(np.asarray(compiled(np.array([0.0])), dtype=float)[0]) if x_min <= 0 <= x_max else None
        analysis.extend(_analyze_function(short, xs, ys, breaks, y0))

    for i in range(len(curves)):
        for j in range(i + 1, len(curves)):
            diff = curves[i][1] - curves[j][1]
            points = _sign_changes(xs, diff)
            if points:
                shown = ", ".join(f"x ≈ {_fmt(p)}" for p in points[:6])
                analysis.append(f"{curves[i][0]} e {curves[j][0]} se cruzam em {shown}.")

    if spec.get("y_min") is not None or spec.get("y_max") is not None:
        lo = _number(spec["y_min"]) if spec.get("y_min") is not None else None
        hi = _number(spec["y_max"]) if spec.get("y_max") is not None else None
        ax.set_ylim(bottom=lo, top=hi)
    elif all_finite:
        values = np.concatenate(all_finite)
        lo, hi = np.percentile(values, [1, 99])
        if hi - lo < 1e-9:
            lo, hi = lo - 1, hi + 1
        pad = (hi - lo) * 0.12
        ax.set_ylim(lo - pad, hi + pad)
    ax.set_xlim(x_min, x_max)
    if x_min <= 0 <= x_max:
        ax.axvline(0, color=MUTED, linewidth=0.9, alpha=0.7)
    ylo, yhi = ax.get_ylim()
    if ylo <= 0 <= yhi:
        ax.axhline(0, color=MUTED, linewidth=0.9, alpha=0.7)
    spec.setdefault("x_label", "x")
    spec.setdefault("y_label", "y")
    return _finish(fig, ax, spec, escape, legend=True), analysis


def _series_list(spec: Dict[str, Any]) -> List[Dict[str, Any]]:
    series = spec.get("series") or []
    if not isinstance(series, list) or not series:
        raise ChartError("Informe os dados em 'series' (ex.: [{\"label\": \"Vendas\", \"values\": [1, 2, 3]}]).")
    if len(series) > MAX_SERIES:
        raise ChartError(f"No máximo {MAX_SERIES} séries por gráfico.")
    out = []
    for index, item in enumerate(series):
        if not isinstance(item, dict):
            raise ChartError("Cada série deve ser um objeto com 'values'.")
        values = _numbers(item.get("values"), f"Série {index + 1}")
        xs = _numbers(item["x"], f"x da série {index + 1}") if item.get("x") else None
        if xs is not None and len(xs) != len(values):
            raise ChartError(f"Série {index + 1}: 'x' e 'values' precisam ter o mesmo tamanho.")
        out.append({"label": str(item.get("label") or f"Série {index + 1}")[:60], "values": values, "x": xs})
    return out


def _categories(spec: Dict[str, Any], length: int) -> Optional[List[str]]:
    cats = spec.get("categories")
    if not cats:
        return None
    if not isinstance(cats, list) or len(cats) > MAX_CATEGORIES:
        raise ChartError(f"'categories' deve ser uma lista com até {MAX_CATEGORIES} rótulos.")
    cats = [str(c)[:40] for c in cats]
    if len(cats) != length:
        raise ChartError("O número de 'categories' deve ser igual ao número de valores de cada série.")
    return cats


def _stats(label: str, values: Sequence[float]) -> str:
    total = sum(values)
    mean = total / len(values)
    return (
        f"{label}: mínimo {_fmt(min(values))}, máximo {_fmt(max(values))}, média {_fmt(mean)}, "
        f"soma {_fmt(total)} ({len(values)} valores)."
    )


def _draw_data(spec: Dict[str, Any], escape: bool) -> Tuple[bytes, List[str]]:
    chart_type = spec["chart_type"]
    series = _series_list(spec)
    fig, ax = _new_figure()
    analysis: List[str] = []

    def lab(text: str) -> str:
        return _escape_mathtext(text) if escape else text

    if chart_type == "pie":
        values = series[0]["values"]
        if any(v < 0 for v in values) or sum(values) <= 0:
            raise ChartError("Gráfico de pizza precisa de valores positivos.")
        cats = _categories(spec, len(values)) or [f"Item {i + 1}" for i in range(len(values))]
        ax.grid(False)
        wedges, texts, autotexts = ax.pie(
            values, labels=[lab(c) for c in cats], autopct="%1.1f%%", startangle=90, counterclock=False,
            colors=[PALETTE[i % len(PALETTE)] for i in range(len(values))],
            wedgeprops={"edgecolor": BG, "linewidth": 2}, textprops={"color": FG, "fontsize": 9},
        )
        for t in autotexts:
            t.set_color("#04140d")
            t.set_fontweight("bold")
        total = sum(values)
        analysis.append(
            "Participação: " + "; ".join(f"{c} {v / total * 100:.1f}%".replace(".", ",") for c, v in zip(cats, values)) + "."
        )
        return _finish(fig, ax, spec, escape, legend=False), analysis

    if chart_type == "histogram":
        values = series[0]["values"]
        bins = spec.get("bins")
        bins = int(bins) if isinstance(bins, (int, float)) and 1 <= int(bins) <= 200 else "auto"
        ax.hist(values, bins=bins, color=PALETTE[0], edgecolor=BG, linewidth=1)
        analysis.append(_stats(series[0]["label"], values))
        spec.setdefault("y_label", "Frequência")
        return _finish(fig, ax, spec, escape, legend=False), analysis

    length = len(series[0]["values"])
    cats = _categories(spec, length) if chart_type in ("bar", "line") else None
    if cats and any(len(s["values"]) != length for s in series):
        raise ChartError("Todas as séries devem ter o mesmo número de valores das categorias.")

    if chart_type == "bar":
        import numpy as np

        positions = np.arange(length)
        width = 0.8 / len(series)
        for i, s in enumerate(series):
            offset = (i - (len(series) - 1) / 2) * width
            bars = ax.bar(positions + offset, s["values"], width=width * 0.95, color=PALETTE[i % len(PALETTE)],
                          label=lab(s["label"]))
            if length * len(series) <= 24:
                ax.bar_label(bars, labels=[_fmt(v) for v in s["values"]], color=MUTED, fontsize=8, padding=2)
        ax.set_xticks(positions)
        ax.set_xticklabels([lab(c) for c in cats] if cats else [str(i + 1) for i in range(length)],
                           rotation=30 if length > 6 else 0, ha="right" if length > 6 else "center")
        ax.grid(axis="x", visible=False)
    else:
        for i, s in enumerate(series):
            xs = s["x"] if s["x"] is not None else list(range(len(s["values"])))
            color = PALETTE[i % len(PALETTE)]
            if chart_type == "scatter":
                ax.scatter(xs, s["values"], color=color, s=30, label=lab(s["label"]), edgecolors=BG, linewidths=0.8)
            else:
                ax.plot(xs, s["values"], color=color, linewidth=2.2, marker="o" if len(xs) <= 40 else None,
                        markersize=4, label=lab(s["label"]))
        if cats and all(s["x"] is None for s in series):
            ax.set_xticks(list(range(length)))
            ax.set_xticklabels([lab(c) for c in cats], rotation=30 if length > 6 else 0,
                               ha="right" if length > 6 else "center")
    for s in series:
        analysis.append(_stats(s["label"], s["values"]))
    return _finish(fig, ax, spec, escape, legend=len(series) > 1), analysis


def render_chart(spec: Dict[str, Any]) -> Tuple[bytes, List[str]]:
    chart_type = str(spec.get("chart_type") or "").strip().lower()
    if chart_type not in CHART_TYPES:
        raise ChartError(f"chart_type deve ser um destes: {', '.join(CHART_TYPES)}.")
    spec = dict(spec, chart_type=chart_type)
    draw = _draw_functions if chart_type == "function" else _draw_data
    try:
        return draw(dict(spec), escape=False)
    except ChartError:
        raise
    except Exception:
        # Textos com $...$ inválido para o mathtext: tenta de novo sem fórmulas.
        return draw(dict(spec), escape=True)


def warm_up() -> None:
    """Carrega o matplotlib (e o cache de fontes) antes do primeiro gráfico."""
    try:
        render_chart({"chart_type": "line", "series": [{"values": [0, 1]}]})
    except Exception:
        pass


class CreateChartTool(BaseTool):
    name = "create_chart"
    accepts_context = True
    description = (
        "Desenha um gráfico em PNG que aparece automaticamente para o usuário e devolve uma análise numérica "
        "(raízes, extremos, interseções, estatísticas). Tipos: 'function' (funções de x: use 'functions', "
        "com notação como x**2 - 4*x + 3, 2x, sen(x), sqrt(x), log(x), e 'x_min'/'x_max'), 'line', 'bar', "
        "'scatter', 'pie' e 'histogram' (use 'series' com 'values', e 'categories' para os rótulos)."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "chart_type": {"type": "string", "enum": list(CHART_TYPES), "description": "Tipo do gráfico."},
            "title": {"type": "string", "description": "Título do gráfico."},
            "x_label": {"type": "string", "description": "Rótulo do eixo x."},
            "y_label": {"type": "string", "description": "Rótulo do eixo y."},
            "functions": {
                "type": "array",
                "description": "Para chart_type='function': até 6 funções de x.",
                "items": {
                    "type": "object",
                    "properties": {
                        "expression": {"type": "string", "description": "Expressão em x, ex.: 'x**2 - 4*x + 3' ou '2*sin(x)'."},
                        "label": {"type": "string", "description": "Legenda, ex.: 'f(x) = x² - 4x + 3'."},
                    },
                    "required": ["expression"],
                },
            },
            "x_min": {"type": "number", "description": "Início do eixo x (funções). Padrão: -10."},
            "x_max": {"type": "number", "description": "Fim do eixo x (funções). Padrão: 10."},
            "y_min": {"type": "number", "description": "Limite inferior do eixo y (opcional)."},
            "y_max": {"type": "number", "description": "Limite superior do eixo y (opcional)."},
            "categories": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Rótulos do eixo x (barras e linhas) ou das fatias (pizza).",
            },
            "series": {
                "type": "array",
                "description": "Séries de dados (até 8). Para pizza e histograma, use só a primeira.",
                "items": {
                    "type": "object",
                    "properties": {
                        "label": {"type": "string", "description": "Nome da série (legenda)."},
                        "values": {"type": "array", "items": {"type": "number"}, "description": "Valores (eixo y)."},
                        "x": {"type": "array", "items": {"type": "number"}, "description": "Valores numéricos do eixo x (opcional; linhas e dispersão)."},
                    },
                    "required": ["values"],
                },
            },
            "bins": {"type": "integer", "description": "Número de classes do histograma (opcional)."},
        },
        "required": ["chart_type"],
    }

    def _run_sync(self, tool_call_id: str = "", **kwargs: Any) -> ToolResult:
        context: ToolContext = kwargs.pop("_context", None) or ToolContext()
        spec = {k: v for k, v in kwargs.items() if not k.startswith("_")}
        try:
            png, analysis = render_chart(spec)
            title = str(spec.get("title") or "grafico")
            att = save_image_bytes(png, f"{_slug(title)}.png", source="chart", owner=context.owner)
        except (ChartError, AttachmentError) as exc:
            return ToolResult(tool_call_id=tool_call_id, name=self.name, status="error", error=str(exc))
        return ToolResult(
            tool_call_id=tool_call_id,
            name=self.name,
            status="success",
            output={
                "message": "Gráfico criado e exibido ao usuário.",
                "image_id": att.id,
                "chart_type": spec.get("chart_type"),
                "analysis": analysis,
            },
            attachments=[att.to_public()],
        )

    async def _run_async_impl(self, tool_call_id: str = "", **kwargs: Any) -> ToolResult:
        return await asyncio.to_thread(self._run_sync, tool_call_id, **kwargs)
