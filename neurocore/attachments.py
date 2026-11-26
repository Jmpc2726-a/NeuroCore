"""
Anexos do NeuroCore: imagens e documentos enviados pelo usuário ou gerados
pelas ferramentas (gráficos, imagens geradas, figuras das questões do ENEM).

Segurança:
- O tipo do arquivo é decidido pelo conteúdo (assinatura), nunca pelo nome
  ou pelo Content-Type informado pelo navegador.
- Toda imagem é decodificada e reescrita com o Pillow: isso descarta
  metadados (EXIF, localização GPS), corrige a rotação de fotos de celular,
  limita a resolução e impede que arquivos malformados cheguem aos modelos.
- SVG e outros formatos que podem conter scripts não são aceitos.
- Os arquivos ficam em <workspace>/.uploads, pasta bloqueada para as
  ferramentas do agente, com nomes aleatórios de 128 bits.
"""

from __future__ import annotations

import base64
import hashlib
import io
import ipaddress
import json
import os
import re
import socket
import time
import unicodedata
import uuid
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urljoin, urlparse

from pydantic import BaseModel

from neurocore.config import get_settings

ATTACHMENT_ID_RE = re.compile(r"^[a-f0-9]{32}$")

MAX_IMAGE_SIDE = 2048            # lado maior após o redimensionamento
MAX_IMAGE_PIXELS = 50_000_000    # proteção contra "bombas" de descompressão
MAX_ENCODED_IMAGE_BYTES = 4_500_000  # a Anthropic recusa imagens acima de 5 MB
MAX_DOC_CHARS = 120_000
MAX_PDF_PAGES = 60
MAX_REMOTE_IMAGE_BYTES = 8 * 1024 * 1024

IMAGE_EXTENSIONS = {"image/png": ".png", "image/jpeg": ".jpg"}

TEXT_EXTENSIONS = {
    ".txt", ".md", ".markdown", ".rst", ".tex", ".csv", ".tsv", ".json", ".jsonl", ".xml",
    ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf", ".log", ".env.example",
    ".py", ".pyi", ".ipynb", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".vue", ".svelte",
    ".java", ".kt", ".kts", ".scala", ".groovy", ".c", ".h", ".cpp", ".cc", ".hpp", ".cs",
    ".go", ".rs", ".rb", ".php", ".swift", ".dart", ".lua", ".pl", ".r", ".m", ".jl",
    ".sql", ".sh", ".bash", ".zsh", ".ps1", ".bat", ".cmd", ".html", ".htm", ".css",
    ".scss", ".sass", ".less", ".graphql", ".proto", ".dockerfile", ".gitignore", ".srt",
}
TEXT_MIME_TYPES = {
    "application/json", "application/xml", "application/x-yaml", "application/yaml",
    "application/javascript", "application/x-python", "application/x-sh", "application/sql",
}
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


class AttachmentError(ValueError):
    """Erro de validação de um anexo, com o status HTTP adequado."""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


class Attachment(BaseModel):
    id: str
    kind: str  # "image" | "document"
    name: str
    mime_type: str
    size_bytes: int = 0
    width: Optional[int] = None
    height: Optional[int] = None
    pages: Optional[int] = None
    text_chars: Optional[int] = None
    truncated: bool = False
    source: str = "upload"  # upload | chart | generated | enem

    @property
    def url(self) -> str:
        return f"/api/v1/files/{self.id}"

    def to_public(self) -> Dict[str, Any]:
        data = self.model_dump()
        data["url"] = self.url
        return data


# ---------------------------------------------------------------------------
# Armazenamento
# ---------------------------------------------------------------------------

def uploads_dir() -> Path:
    path = Path(get_settings().UPLOADS_DIR)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _meta_path(attachment_id: str) -> Path:
    return uploads_dir() / f"{attachment_id}.json"


def _safe_name(name: Optional[str], default: str) -> str:
    raw = os.path.basename((name or "").replace("\\", "/")).strip()
    raw = unicodedata.normalize("NFC", raw)
    raw = "".join(ch for ch in raw if ch.isprintable() and ch not in '<>:"/\\|?*')
    raw = raw.strip(" .")
    return (raw[:120] or default)


def _write_attachment(att: Attachment, payload: bytes, extension: str, owner: Optional[str]) -> Attachment:
    folder = uploads_dir()
    data_file = f"{att.id}{extension}"
    (folder / data_file).write_bytes(payload)
    meta = att.model_dump()
    meta.update({"owner": owner, "created_at": time.time(), "file": data_file})
    tmp = folder / f"{att.id}.json.tmp"
    tmp.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, _meta_path(att.id))
    _enforce_retention()
    return att


def _read_meta(attachment_id: str) -> Optional[Dict[str, Any]]:
    if not ATTACHMENT_ID_RE.fullmatch(attachment_id or ""):
        return None
    try:
        return json.loads(_meta_path(attachment_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _data_path(meta: Dict[str, Any]) -> Optional[Path]:
    name = str(meta.get("file") or "")
    if not re.fullmatch(r"[a-f0-9]{32}\.(png|jpg|txt)", name):
        return None
    path = uploads_dir() / name
    return path if path.is_file() else None


def get_attachment(attachment_id: str, owner: Optional[str] = None, check_owner: bool = True) -> Optional[Attachment]:
    """Devolve o anexo se existir e (quando check_owner) se pertencer ao dono.

    Anexos sem dono (ex.: figuras públicas do ENEM) podem ser usados por todos.
    """
    meta = _read_meta(attachment_id)
    if not meta or _data_path(meta) is None:
        return None
    if check_owner and meta.get("owner") and meta.get("owner") != owner:
        return None
    try:
        return Attachment(**{k: v for k, v in meta.items() if k in Attachment.model_fields})
    except Exception:
        return None


def get_attachment_file(attachment_id: str) -> Optional[Tuple[Attachment, Path]]:
    att = get_attachment(attachment_id, check_owner=False)
    if att is None:
        return None
    meta = _read_meta(attachment_id) or {}
    path = _data_path(meta)
    return (att, path) if path else None


@lru_cache(maxsize=64)
def _cached_base64(attachment_id: str, mtime: float) -> str:
    found = get_attachment_file(attachment_id)
    if not found:
        raise FileNotFoundError(attachment_id)
    return base64.b64encode(found[1].read_bytes()).decode("ascii")


def read_image_base64(att: Attachment) -> Optional[str]:
    found = get_attachment_file(att.id)
    if not found or att.kind != "image":
        return None
    try:
        return _cached_base64(att.id, found[1].stat().st_mtime)
    except OSError:
        return None


def read_image_bytes(att: Attachment) -> Optional[bytes]:
    found = get_attachment_file(att.id)
    if not found or att.kind != "image":
        return None
    try:
        return found[1].read_bytes()
    except OSError:
        return None


def read_document_text(att: Attachment) -> str:
    found = get_attachment_file(att.id)
    if not found or att.kind != "document":
        return ""
    try:
        return found[1].read_text(encoding="utf-8")
    except OSError:
        return ""


def delete_attachment(attachment_id: str) -> bool:
    meta = _read_meta(attachment_id)
    if not meta:
        return False
    path = _data_path(meta)
    for target in (path, _meta_path(attachment_id)):
        if target is not None:
            try:
                target.unlink()
            except OSError:
                pass
    return True


def delete_owned_attachments(attachment_ids: Iterable[str], owner: Optional[str]) -> int:
    """Apaga os anexos da lista que pertencem ao dono (nunca os sem dono)."""
    if not owner:
        return 0
    removed = 0
    for att_id in set(attachment_ids):
        meta = _read_meta(att_id)
        if meta and meta.get("owner") == owner and delete_attachment(att_id):
            removed += 1
    return removed


def _enforce_retention() -> None:
    limit = get_settings().MAX_UPLOAD_FILES
    if limit <= 0:
        return
    try:
        metas = sorted(uploads_dir().glob("*.json"), key=lambda p: p.stat().st_mtime)
    except OSError:
        return
    for old in metas[: max(0, len(metas) - limit)]:
        delete_attachment(old.stem)


# ---------------------------------------------------------------------------
# Detecção de tipo
# ---------------------------------------------------------------------------

def sniff_image(data: bytes) -> Optional[str]:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    if data[:2] == b"BM":
        return "bmp"
    return None


def _is_heic(data: bytes) -> bool:
    return data[4:8] == b"ftyp" and data[8:12] in (b"heic", b"heix", b"hevc", b"heim", b"heis", b"mif1", b"msf1", b"avif")


# ---------------------------------------------------------------------------
# Imagens
# ---------------------------------------------------------------------------

def _encode_image(img, as_jpeg: bool) -> Tuple[bytes, str]:
    buf = io.BytesIO()
    if as_jpeg:
        img.save(buf, format="JPEG", quality=88, optimize=True)
        return buf.getvalue(), "image/jpeg"
    img.save(buf, format="PNG", compress_level=6)
    return buf.getvalue(), "image/png"


def process_image(data: bytes) -> Tuple[bytes, str, int, int]:
    """Valida e reescreve a imagem. Devolve (bytes, mime, largura, altura)."""
    from PIL import Image, ImageOps

    kind = sniff_image(data)
    if kind is None:
        if _is_heic(data):
            raise AttachmentError(
                "Imagens HEIC/AVIF não são suportadas. No iPhone, envie como JPEG "
                "(Ajustes > Câmera > Formatos > Mais Compatível) ou tire um print.",
                415,
            )
        raise AttachmentError("Formato de imagem não suportado. Use PNG, JPEG, WEBP, GIF ou BMP.", 415)

    try:
        with Image.open(io.BytesIO(data)) as probe:
            width, height = probe.size
            if width <= 0 or height <= 0 or width * height > MAX_IMAGE_PIXELS:
                raise AttachmentError("A imagem tem resolução grande demais.", 413)
            probe.verify()
    except AttachmentError:
        raise
    except Exception:
        raise AttachmentError("Não foi possível ler a imagem (arquivo corrompido ou formato inválido).", 415)

    try:
        img = Image.open(io.BytesIO(data))
        try:
            img.seek(0)  # GIF/WEBP animados: usa o primeiro quadro
        except EOFError:
            pass
        img.load()
        img = ImageOps.exif_transpose(img)
        has_alpha = img.mode in ("RGBA", "LA", "PA") or (img.mode == "P" and "transparency" in img.info)
        img = img.convert("RGBA" if has_alpha else "RGB")
        if max(img.size) > MAX_IMAGE_SIDE:
            img.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE), Image.Resampling.LANCZOS)

        # Fotos continuam JPEG; prints, desenhos e imagens com transparência viram PNG.
        as_jpeg = kind == "jpeg" and not has_alpha
        payload, mime = _encode_image(img, as_jpeg)
        while len(payload) > MAX_ENCODED_IMAGE_BYTES and max(img.size) > 512:
            if not as_jpeg and not has_alpha:
                as_jpeg = True
            else:
                img = img.resize((max(1, int(img.width * 0.8)), max(1, int(img.height * 0.8))), Image.Resampling.LANCZOS)
            payload, mime = _encode_image(img, as_jpeg)
        return payload, mime, img.width, img.height
    except AttachmentError:
        raise
    except Exception:
        raise AttachmentError("Não foi possível processar a imagem.", 415)


def save_image_bytes(
    data: bytes,
    name: Optional[str] = None,
    source: str = "upload",
    owner: Optional[str] = None,
    attachment_id: Optional[str] = None,
) -> Attachment:
    if not data:
        raise AttachmentError("Arquivo vazio.")
    payload, mime, width, height = process_image(data)
    att_id = attachment_id if attachment_id and ATTACHMENT_ID_RE.fullmatch(attachment_id) else uuid.uuid4().hex
    ext = IMAGE_EXTENSIONS[mime]
    display = _safe_name(name, f"imagem{ext}")
    stem, old_ext = os.path.splitext(display)
    if old_ext.lower() not in (".png", ".jpg", ".jpeg"):
        display = f"{stem or 'imagem'}{ext}"
    att = Attachment(
        id=att_id,
        kind="image",
        name=display,
        mime_type=mime,
        size_bytes=len(payload),
        width=width,
        height=height,
        source=source,
    )
    return _write_attachment(att, payload, ext, owner)


# ---------------------------------------------------------------------------
# Documentos
# ---------------------------------------------------------------------------

def _decode_text(data: bytes) -> str:
    if b"\x00" in data[:4096]:
        raise AttachmentError("O arquivo parece ser binário; envie texto, PDF, Word ou imagem.", 415)
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            text = data.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        text = data.decode("latin-1")
    sample = text[:4000]
    if sample:
        bad = sum(1 for ch in sample if not (ch.isprintable() or ch in "\n\r\t"))
        if bad / len(sample) > 0.1:
            raise AttachmentError("O arquivo parece ser binário; envie texto, PDF, Word ou imagem.", 415)
    return text


def _pdf_text(data: bytes) -> Tuple[str, int]:
    try:
        from pypdf import PdfReader
    except ImportError:  # pragma: no cover - dependência listada em requirements
        raise AttachmentError("Leitura de PDF indisponível neste servidor (instale pypdf).", 415)
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            try:
                reader.decrypt("")
            except Exception:
                raise AttachmentError("O PDF está protegido por senha.", 415)
        total_pages = len(reader.pages)
        parts: List[str] = []
        size = 0
        for index, page in enumerate(reader.pages):
            if index >= MAX_PDF_PAGES or size > MAX_DOC_CHARS:
                parts.append(f"[... páginas restantes não lidas: {total_pages - index}]")
                break
            try:
                text = (page.extract_text() or "").strip()
            except Exception:
                text = ""
            parts.append(f"--- Página {index + 1} ---\n{text}")
            size += len(text)
        return "\n\n".join(parts), total_pages
    except AttachmentError:
        raise
    except Exception:
        raise AttachmentError("Não foi possível ler o PDF (arquivo corrompido ou formato inválido).", 415)


def _docx_text(data: bytes) -> str:
    try:
        from docx import Document
    except ImportError:  # pragma: no cover
        raise AttachmentError("Leitura de Word indisponível neste servidor (instale python-docx).", 415)
    try:
        doc = Document(io.BytesIO(data))
        lines = [p.text for p in doc.paragraphs]
        for table in doc.tables:
            lines.append("")
            for row in table.rows:
                lines.append(" | ".join(cell.text.strip() for cell in row.cells))
        return "\n".join(lines).strip()
    except Exception:
        raise AttachmentError("Não foi possível ler o arquivo do Word (.docx).", 415)


def save_document_bytes(
    data: bytes,
    name: Optional[str] = None,
    content_type: Optional[str] = None,
    owner: Optional[str] = None,
) -> Attachment:
    if not data:
        raise AttachmentError("Arquivo vazio.")
    display = _safe_name(name, "documento.txt")
    lower = display.lower()
    ext = os.path.splitext(lower)[1]
    declared = (content_type or "").split(";")[0].strip().lower()
    pages: Optional[int] = None

    if data.startswith(b"%PDF-"):
        text, pages = _pdf_text(data)
        mime = "application/pdf"
        if not re.sub(r"--- Página \d+ ---|\s", "", text):
            text += (
                "\n\n[Este PDF não tem texto extraível (provavelmente é digitalizado). "
                "Para analisar o conteúdo, peça ao usuário que envie as páginas como imagens.]"
            )
    elif data.startswith(b"PK\x03\x04"):
        if ext != ".docx":
            raise AttachmentError("Arquivos compactados não são suportados. Envie .docx, PDF, texto ou imagem.", 415)
        text = _docx_text(data)
        mime = DOCX_MIME
    elif ext in TEXT_EXTENSIONS or lower.endswith(".env.example") or declared.startswith("text/") or declared in TEXT_MIME_TYPES:
        text = _decode_text(data)
        mime = "text/plain"
    elif ext in (".doc", ".xls", ".xlsx", ".ppt", ".pptx", ".odt"):
        raise AttachmentError("Formato não suportado. Salve como PDF ou .docx e envie novamente.", 415)
    else:
        raise AttachmentError("Tipo de arquivo não suportado. Envie imagens, PDF, Word (.docx) ou arquivos de texto/código.", 415)

    text = text.replace("\r\n", "\n")
    truncated = len(text) > MAX_DOC_CHARS
    if truncated:
        original = len(text)
        text = text[:MAX_DOC_CHARS] + f"\n\n[... conteúdo truncado: {MAX_DOC_CHARS} de {original} caracteres]"

    att = Attachment(
        id=uuid.uuid4().hex,
        kind="document",
        name=display,
        mime_type=mime,
        size_bytes=len(data),
        pages=pages,
        text_chars=len(text),
        truncated=truncated,
        source="upload",
    )
    return _write_attachment(att, text.encode("utf-8"), ".txt", owner)


def save_upload(
    data: bytes,
    filename: Optional[str] = None,
    content_type: Optional[str] = None,
    owner: Optional[str] = None,
) -> Attachment:
    """Ponto de entrada dos uploads: decide entre imagem e documento pelo conteúdo."""
    if not data:
        raise AttachmentError("Arquivo vazio.")
    if len(data) > get_settings().MAX_UPLOAD_BYTES:
        raise AttachmentError("Arquivo grande demais.", 413)
    lower = (filename or "").lower()
    if lower.endswith((".svg", ".svgz")):
        # SVG pode conter scripts; imagens são sempre servidas como PNG/JPEG.
        raise AttachmentError("Imagens SVG não são aceitas. Envie PNG ou JPEG.", 415)
    if sniff_image(data[:32]) or _is_heic(data[:16]):
        return save_image_bytes(data, filename, source="upload", owner=owner)
    return save_document_bytes(data, filename, content_type, owner)


# ---------------------------------------------------------------------------
# Imagens remotas (figuras das questões do ENEM)
# ---------------------------------------------------------------------------

def _host_allowed(host: str, allowed: Iterable[str]) -> bool:
    host = (host or "").lower().rstrip(".")
    return any(host == h.lower() or host.endswith("." + h.lower()) for h in allowed if h)


def _host_resolves_to_public(host: str) -> bool:
    """Defesa extra contra SSRF: o host não pode apontar para a rede interna.

    Se a resolução local falhar (ambientes que só saem por proxy), deixa o
    proxy resolver: o host já passou pela lista de servidores permitidos.
    """
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return True
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            return False
        if not ip.is_global:
            return False
    return True


def remote_image_id(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:32]


def save_remote_image(
    url: str,
    source: str = "enem",
    allowed_hosts: Optional[List[str]] = None,
    name: Optional[str] = None,
) -> Attachment:
    """Baixa uma imagem de um servidor permitido e a guarda como anexo público.

    O id é derivado da URL, então a mesma figura é baixada só uma vez.
    """
    import httpx

    allowed = allowed_hosts if allowed_hosts is not None else get_settings().ENEM_IMAGE_HOSTS
    att_id = remote_image_id(url)
    existing = get_attachment(att_id, check_owner=False)
    if existing:
        return existing

    current = url
    with httpx.Client(timeout=httpx.Timeout(20.0, connect=8.0), follow_redirects=False) as client:
        for _ in range(4):
            parsed = urlparse(current)
            if parsed.scheme not in ("http", "https") or not parsed.hostname:
                raise AttachmentError("URL de imagem inválida.")
            if not _host_allowed(parsed.hostname, allowed):
                raise AttachmentError("Servidor de imagem não permitido.")
            if not _host_resolves_to_public(parsed.hostname):
                raise AttachmentError("Servidor de imagem não permitido.")
            with client.stream("GET", current, headers={"Accept": "image/*"}) as resp:
                if resp.status_code in (301, 302, 303, 307, 308):
                    location = resp.headers.get("location")
                    if not location:
                        raise AttachmentError("Redirecionamento inválido.")
                    current = urljoin(current, location)
                    continue
                if resp.status_code != 200:
                    raise AttachmentError(f"Imagem indisponível (HTTP {resp.status_code}).")
                chunks: List[bytes] = []
                total = 0
                for chunk in resp.iter_bytes():
                    total += len(chunk)
                    if total > MAX_REMOTE_IMAGE_BYTES:
                        raise AttachmentError("Imagem remota grande demais.", 413)
                    chunks.append(chunk)
            display = name or os.path.basename(urlparse(current).path) or "figura.png"
            return save_image_bytes(b"".join(chunks), display, source=source, owner=None, attachment_id=att_id)
    raise AttachmentError("Redirecionamentos demais ao baixar a imagem.")
