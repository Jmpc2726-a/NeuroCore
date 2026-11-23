"""
Document creation tool for NeuroCore Agent.

Gera arquivos PDF, Word (.docx) ou Markdown a partir de um texto em
Markdown simples (títulos com #, listas com - e 1., negrito com **).
Os arquivos ficam em <workspace>/generated_docs e são entregues ao usuário
por uma rota de download (veja get_document_path).
"""

from __future__ import annotations

import asyncio
import re
import unicodedata
import uuid
from pathlib import Path
from typing import Any, List, Optional, Tuple, Union

from neurocore.config import get_settings
from neurocore.tools.base import BaseTool, ToolResult

DOCS_DIR_NAME = "generated_docs"
SUPPORTED_FORMATS = ("pdf", "docx", "md")
MAX_CONTENT_CHARS = 200_000
FILENAME_PATTERN = re.compile(r"^[a-z0-9-]{1,80}\.(pdf|docx|md)$")

# Fontes Unicode procuradas (regular, negrito). Se nenhuma existir, o PDF usa
# Helvetica e símbolos matemáticos são trocados por texto (ver FALLBACK_SYMBOLS).
_HERE = Path(__file__).resolve().parent.parent
FONT_CANDIDATES = [
    (_HERE / "fonts" / "DejaVuSans.ttf", _HERE / "fonts" / "DejaVuSans-Bold.ttf"),
    (
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    ),
    (
        Path("/usr/share/fonts/dejavu-sans-fonts/DejaVuSans.ttf"),
        Path("/usr/share/fonts/dejavu-sans-fonts/DejaVuSans-Bold.ttf"),
    ),
    (
        Path("/usr/share/fonts/dejavu/DejaVuSans.ttf"),
        Path("/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf"),
    ),
]

FALLBACK_SYMBOLS = {
    "√": "raiz de ", "π": "pi", "Δ": "Delta", "∆": "Delta", "θ": "theta",
    "λ": "lambda", "ω": "omega", "Ω": "Omega", "μ": "mu", "α": "alfa",
    "β": "beta", "γ": "gama", "σ": "sigma", "ρ": "rho", "φ": "fi",
    "ν": "nu", "τ": "tau", "ε": "epsilon", "η": "eta", "κ": "kappa", "ψ": "psi",
    "Σ": "Sigma", "Φ": "Phi", "Ψ": "Psi",
    "≤": "<=", "≥": ">=", "≠": "!=", "≈": "~", "∞": "infinito",
    "→": "->", "←": "<-", "⇒": "=>", "−": "-", "–": "-", "—": "-",
    "“": '"', "”": '"', "‘": "'", "’": "'", "…": "...", "∑": "soma",
    "∫": "integral", "·": ".", "⁻": "-", "₀": "0", "₁": "1", "₂": "2",
    "₃": "3", "⁴": "^4", "⁵": "^5", "⁶": "^6", "⁷": "^7", "⁸": "^8", "⁹": "^9",
}

# Bloco: (tipo, texto, rótulo). Tipos: h1, h2, h3, bullet, number, para, hr
Block = Tuple[str, str, str]


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------

def _slugify(text: str, max_len: int = 40) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return (text[:max_len].strip("-")) or "documento"


def _strip_unsupported(text: str) -> str:
    """Remove emojis e marcadores invisíveis que viram quadrados no PDF."""
    return "".join(
        ch for ch in text
        if ord(ch) <= 0xFFFF and not (0xFE00 <= ord(ch) <= 0xFE0F) and ord(ch) != 0x200D
    )


def _replace_fallback_symbols(text: str) -> str:
    for src, dst in FALLBACK_SYMBOLS.items():
        text = text.replace(src, dst)
    return text


def _parse_blocks(content: str) -> List[Block]:
    """Converte Markdown simples em uma lista de blocos."""
    blocks: List[Block] = []
    paragraph: List[str] = []

    def flush_paragraph() -> None:
        if paragraph:
            blocks.append(("para", " ".join(paragraph).strip(), ""))
            paragraph.clear()

    for raw in content.replace("\r\n", "\n").split("\n"):
        line = raw.rstrip()
        stripped = line.strip()

        if not stripped:
            flush_paragraph()
            continue
        if re.fullmatch(r"[-*_]{3,}", stripped):
            flush_paragraph()
            blocks.append(("hr", "", ""))
            continue

        heading = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        if heading:
            flush_paragraph()
            level = min(len(heading.group(1)), 3)
            blocks.append((f"h{level}", heading.group(2).strip(), ""))
            continue

        bullet = re.match(r"^[-*+]\s+(.*)$", stripped)
        if bullet:
            flush_paragraph()
            blocks.append(("bullet", bullet.group(1).strip(), ""))
            continue

        number = re.match(r"^(\d+[.)])\s+(.*)$", stripped)
        if number:
            flush_paragraph()
            blocks.append(("number", number.group(2).strip(), number.group(1)))
            continue

        paragraph.append(stripped)

    flush_paragraph()
    return blocks


def _find_unicode_font() -> Optional[Tuple[Path, Path]]:
    for regular, bold in FONT_CANDIDATES:
        if regular.exists() and bold.exists():
            return regular, bold
    return None


# ---------------------------------------------------------------------------
# Geradores
# ---------------------------------------------------------------------------

def _build_pdf(path: Path, title: str, blocks: List[Block]) -> None:
    from xml.sax.saxutils import escape

    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import HRFlowable, Paragraph, SimpleDocTemplate, Spacer

    fonts = _find_unicode_font()
    if fonts:
        pdfmetrics.registerFont(TTFont("NC-Regular", str(fonts[0])))
        pdfmetrics.registerFont(TTFont("NC-Bold", str(fonts[1])))
        pdfmetrics.registerFontFamily(
            "NC-Regular",
            normal="NC-Regular", bold="NC-Bold",
            italic="NC-Regular", boldItalic="NC-Bold",
        )
        base_font, bold_font = "NC-Regular", "NC-Bold"
    else:
        base_font, bold_font = "Helvetica", "Helvetica-Bold"

    def prepare(text: str) -> str:
        text = _strip_unsupported(text)
        if not fonts:
            text = _replace_fallback_symbols(text)
        text = escape(text)
        return re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)

    body = ParagraphStyle("body", fontName=base_font, fontSize=11, leading=16,
                          alignment=TA_LEFT, spaceAfter=6)
    h1 = ParagraphStyle("h1", parent=body, fontName=bold_font, fontSize=16,
                        leading=20, spaceBefore=14, spaceAfter=8)
    h2 = ParagraphStyle("h2", parent=body, fontName=bold_font, fontSize=13,
                        leading=17, spaceBefore=10, spaceAfter=6)
    h3 = ParagraphStyle("h3", parent=body, fontName=bold_font, fontSize=11.5,
                        leading=15, spaceBefore=8, spaceAfter=4)
    title_style = ParagraphStyle("title", parent=body, fontName=bold_font,
                                 fontSize=22, leading=27, spaceAfter=14)
    item = ParagraphStyle("item", parent=body, leftIndent=18, bulletIndent=4,
                          spaceAfter=3)

    story: List[Any] = [Paragraph(prepare(title), title_style)]
    story.append(HRFlowable(width="100%", thickness=0.8, color="#888888"))
    story.append(Spacer(1, 10))

    for kind, text, label in blocks:
        if kind == "hr":
            story.append(HRFlowable(width="100%", thickness=0.5, color="#BBBBBB"))
            story.append(Spacer(1, 6))
        elif kind == "h1":
            story.append(Paragraph(prepare(text), h1))
        elif kind == "h2":
            story.append(Paragraph(prepare(text), h2))
        elif kind == "h3":
            story.append(Paragraph(prepare(text), h3))
        elif kind == "bullet":
            story.append(Paragraph(prepare(text), item, bulletText="\u2022"))
        elif kind == "number":
            story.append(Paragraph(prepare(text), item, bulletText=_strip_unsupported(label)))
        else:
            story.append(Paragraph(prepare(text), body))

    doc = SimpleDocTemplate(
        str(path), pagesize=A4,
        leftMargin=2 * cm, rightMargin=2 * cm, topMargin=2 * cm, bottomMargin=2 * cm,
        title=_strip_unsupported(title), author="NeuroCore",
    )
    doc.build(story)


def _add_bold_runs(paragraph: Any, text: str) -> None:
    parts = re.split(r"\*\*(.+?)\*\*", text)
    for i, part in enumerate(parts):
        if part:
            run = paragraph.add_run(part)
            run.bold = i % 2 == 1


def _build_docx(path: Path, title: str, blocks: List[Block]) -> None:
    from docx import Document

    doc = Document()
    doc.core_properties.title = _strip_unsupported(title)
    doc.core_properties.author = "NeuroCore"
    doc.add_heading(_strip_unsupported(title), level=0)

    for kind, text, label in blocks:
        text = _strip_unsupported(text)
        if kind == "hr":
            doc.add_paragraph("_" * 40)
        elif kind in ("h1", "h2", "h3"):
            doc.add_heading(text, level=int(kind[1]))
        elif kind == "bullet":
            _add_bold_runs(doc.add_paragraph(style="List Bullet"), text)
        elif kind == "number":
            _add_bold_runs(doc.add_paragraph(), f"{label} {text}")
        else:
            _add_bold_runs(doc.add_paragraph(), text)

    doc.save(str(path))


def _build_markdown(path: Path, title: str, content: str) -> None:
    path.write_text(f"# {title}\n\n{content.strip()}\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Rota de download (use no servidor)
# ---------------------------------------------------------------------------

def get_document_path(
    filename: str,
    workspace_root: Optional[Union[str, Path]] = None,
) -> Optional[Path]:
    """
    Devolve o caminho de um documento gerado, ou None se não existir ou se o
    nome for inválido. Aceita apenas nomes simples (sem barras nem '..'),
    então não dá para baixar outros arquivos do servidor por essa função.
    """
    if not FILENAME_PATTERN.fullmatch(filename or ""):
        return None
    root = Path(workspace_root).resolve() if workspace_root else get_settings().workspace_root.resolve()
    docs_dir = (root / DOCS_DIR_NAME).resolve()
    target = (docs_dir / filename).resolve()
    try:
        target.relative_to(docs_dir)
    except ValueError:
        return None
    return target if target.is_file() else None


# ---------------------------------------------------------------------------
# Ferramenta
# ---------------------------------------------------------------------------

class CreateDocumentTool(BaseTool):
    name = "create_document"
    description = (
        "Cria um documento para o aluno baixar (PDF, Word ou Markdown), como lista de "
        "exercícios, resumo de estudo ou plano semanal. Passe o conteúdo em Markdown "
        "simples: títulos com #, listas com - ou 1., negrito com **texto**. "
        "Devolve o link de download para informar ao aluno."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "description": "Título do documento (ex.: 'Lista de exercícios - Ondas').",
            },
            "content": {
                "type": "string",
                "description": "Conteúdo completo do documento, em Markdown simples.",
            },
            "format": {
                "type": "string",
                "enum": list(SUPPORTED_FORMATS),
                "description": "Formato do arquivo. Padrão: pdf.",
            },
        },
        "required": ["title", "content"],
    }

    def _run_sync(
        self,
        tool_call_id: str = "",
        title: str = "",
        content: str = "",
        format: str = "pdf",
        **kwargs: Any,
    ) -> ToolResult:
        title = (title or "").strip()
        content = content or ""
        fmt = (format or "pdf").lower().lstrip(".")

        if not title:
            raise ValueError("O título do documento é obrigatório.")
        if not content.strip():
            raise ValueError("O conteúdo do documento está vazio.")
        if len(content) > MAX_CONTENT_CHARS:
            raise ValueError(f"Conteúdo grande demais (máximo {MAX_CONTENT_CHARS} caracteres).")
        if fmt not in SUPPORTED_FORMATS:
            raise ValueError(f"Formato '{fmt}' não suportado. Use: {', '.join(SUPPORTED_FORMATS)}.")

        docs_dir = self.validate_path(DOCS_DIR_NAME)
        docs_dir.mkdir(parents=True, exist_ok=True)

        filename = f"{_slugify(title)}-{uuid.uuid4().hex[:6]}.{fmt}"
        path = self.validate_path(f"{DOCS_DIR_NAME}/{filename}")

        blocks = _parse_blocks(content)
        if fmt == "pdf":
            _build_pdf(path, title, blocks)
        elif fmt == "docx":
            _build_docx(path, title, blocks)
        else:
            _build_markdown(path, title, content)

        return ToolResult(
            tool_call_id=tool_call_id,
            name=self.name,
            status="success",
            output={
                "filename": filename,
                "format": fmt,
                "size_bytes": path.stat().st_size,
                "download_url": f"/api/documents/{filename}",
                "message": (
                    "Documento criado com sucesso. Diga ao aluno que ele pode baixar "
                    "pelo link em download_url."
                ),
            },
        )

    async def _run_async_impl(self, tool_call_id: str = "", **kwargs: Any) -> ToolResult:
        # Gerar o arquivo é trabalho de CPU/disco; roda em outra thread.
        return await asyncio.to_thread(self._run_sync, tool_call_id, **kwargs)
