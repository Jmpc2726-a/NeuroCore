"""
Geração (e edição) de imagens para o NeuroCore.

Usa, nesta ordem de preferência:
1. o provedor escolhido pelo usuário, se for OpenAI, Gemini ou OpenRouter e
   ele tiver informado a própria chave;
2. as chaves configuradas no servidor (Gemini, OpenAI, OpenRouter).

A Anthropic não gera imagens; quem usa Claude só terá geração de imagens se
o servidor tiver uma das outras chaves.
"""

from __future__ import annotations

import base64
import re
import unicodedata
from typing import Any, List, Optional, Tuple

from neurocore.attachments import AttachmentError, get_attachment, read_image_bytes, save_image_bytes
from neurocore.config import get_settings
from neurocore.http_utils import ProviderError, post_json, post_multipart
from neurocore.tools.base import BaseTool, ToolResult
from neurocore.tools.context import ToolContext

ASPECT_RATIOS = ("1:1", "16:9", "9:16", "4:3", "3:4")
IMAGE_TIMEOUT = 180.0
MAX_REFERENCES = 3

_OPENAI_SIZES = {"1:1": "1024x1024", "16:9": "1536x1024", "4:3": "1536x1024", "9:16": "1024x1536", "3:4": "1024x1536"}
_DALLE3_SIZES = {"1:1": "1024x1024", "16:9": "1792x1024", "4:3": "1792x1024", "9:16": "1024x1792", "3:4": "1024x1792"}
_DATA_URL_RE = re.compile(r"^data:(image/[\w.+-]+);base64,(.+)$", re.DOTALL)

Reference = Tuple[str, bytes]  # (mime, bytes)


def resolve_image_backend(context: Optional[ToolContext]) -> Optional[Tuple[str, str]]:
    """Escolhe (backend, chave) para gerar imagens, ou None se não houver."""
    settings = get_settings()
    if not settings.ENABLE_IMAGE_GENERATION:
        return None
    if context and context.api_key and context.provider in ("openai", "gemini", "openrouter"):
        return context.provider, context.api_key
    for name, key in (
        ("gemini", settings.gemini_api_key),
        ("openai", settings.openai_api_key),
        ("openrouter", settings.openrouter_api_key),
    ):
        if key and key.strip():
            return name, key.strip()
    return None


def _decode_b64(data: str) -> bytes:
    try:
        return base64.b64decode(data, validate=False)
    except (ValueError, TypeError):
        raise ProviderError("Imagem devolvida em formato inválido.")


def _slug(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return text[:40] or "imagem"


# ---------------------------------------------------------------------------
# Backends
# ---------------------------------------------------------------------------

async def _gemini_image(prompt: str, aspect: str, refs: List[Reference], key: str) -> Tuple[bytes, str]:
    model = get_settings().GEMINI_IMAGE_MODEL
    if not re.fullmatch(r"[A-Za-z0-9._\-]+", model):
        raise ProviderError("Gemini: nome de modelo de imagem inválido.")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    parts: List[dict] = [
        {"inlineData": {"mimeType": mime, "data": base64.b64encode(data).decode("ascii")}} for mime, data in refs
    ]
    parts.append({"text": prompt})
    payload: dict = {
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {"responseModalities": ["TEXT", "IMAGE"]},
    }
    if aspect != "1:1":
        payload["generationConfig"]["imageConfig"] = {"aspectRatio": aspect}
    headers = {"x-goog-api-key": key, "Content-Type": "application/json"}
    try:
        data = await post_json("Gemini", url, payload, headers, timeout=IMAGE_TIMEOUT)
    except ProviderError as exc:
        if aspect == "1:1" or exc.status_code != 400:
            raise
        payload["generationConfig"].pop("imageConfig", None)  # modelo sem suporte a proporção
        data = await post_json("Gemini", url, payload, headers, timeout=IMAGE_TIMEOUT)

    texts: List[str] = []
    for cand in data.get("candidates") or []:
        for part in (cand.get("content") or {}).get("parts") or []:
            inline = part.get("inlineData") or part.get("inline_data")
            if inline and inline.get("data"):
                return _decode_b64(inline["data"]), model
            if part.get("text"):
                texts.append(part["text"])
    reason = (data.get("promptFeedback") or {}).get("blockReason")
    detail = f" (bloqueio: {reason})" if reason else (f": {' '.join(texts)[:300]}" if texts else "")
    raise ProviderError(f"O Gemini não devolveu imagem{detail}")


async def _openai_image(prompt: str, aspect: str, refs: List[Reference], key: str) -> Tuple[bytes, str]:
    model = get_settings().OPENAI_IMAGE_MODEL
    headers = {"Authorization": f"Bearer {key}"}

    async def generate(model_name: str) -> dict:
        if refs:
            field = "image[]" if len(refs) > 1 else "image"
            files = [(field, (f"referencia-{i + 1}.{'png' if mime == 'image/png' else 'jpg'}", data, mime))
                     for i, (mime, data) in enumerate(refs)]
            form = {"model": model_name, "prompt": prompt, "size": _OPENAI_SIZES[aspect], "n": "1"}
            return await post_multipart("OpenAI", "https://api.openai.com/v1/images/edits", form, files, headers,
                                        timeout=IMAGE_TIMEOUT)
        payload: dict = {"model": model_name, "prompt": prompt, "n": 1}
        if model_name.startswith("dall-e"):
            payload.update({"size": _DALLE3_SIZES[aspect], "response_format": "b64_json"})
        else:
            payload["size"] = _OPENAI_SIZES[aspect]
        return await post_json("OpenAI", "https://api.openai.com/v1/images/generations", payload,
                               dict(headers, **{"Content-Type": "application/json"}), timeout=IMAGE_TIMEOUT)

    try:
        data = await generate(model)
        used = model
    except ProviderError as exc:
        # gpt-image-1 pode exigir verificação da organização; o dall-e-3 não.
        if refs or model.startswith("dall-e") or exc.status_code not in (403, 404):
            raise
        data = await generate("dall-e-3")
        used = "dall-e-3"
    items = data.get("data") or []
    if not items or not items[0].get("b64_json"):
        raise ProviderError("A OpenAI não devolveu imagem.")
    return _decode_b64(items[0]["b64_json"]), used


async def _openrouter_image(prompt: str, aspect: str, refs: List[Reference], key: str) -> Tuple[bytes, str]:
    model = get_settings().OPENROUTER_IMAGE_MODEL
    content: List[dict] = [{"type": "text", "text": prompt}]
    for mime, data in refs:
        content.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"}})
    payload: dict = {"model": model, "messages": [{"role": "user", "content": content}], "modalities": ["image", "text"]}
    if aspect != "1:1":
        payload["image_config"] = {"aspect_ratio": aspect}
    headers = {"Authorization": f"Bearer {key}", "X-Title": "NeuroCore Agent", "Content-Type": "application/json"}
    url = "https://openrouter.ai/api/v1/chat/completions"
    try:
        data = await post_json("OpenRouter", url, payload, headers, timeout=IMAGE_TIMEOUT)
    except ProviderError as exc:
        if aspect == "1:1" or exc.status_code != 400:
            raise
        payload.pop("image_config", None)
        data = await post_json("OpenRouter", url, payload, headers, timeout=IMAGE_TIMEOUT)
    for choice in data.get("choices") or []:
        for image in (choice.get("message") or {}).get("images") or []:
            url_value = ((image or {}).get("image_url") or {}).get("url") or ""
            match = _DATA_URL_RE.match(url_value)
            if match:
                return _decode_b64(match.group(2)), model
    raise ProviderError("O OpenRouter não devolveu imagem (o modelo escolhido gera imagens?).")


_BACKENDS = {"gemini": _gemini_image, "openai": _openai_image, "openrouter": _openrouter_image}


# ---------------------------------------------------------------------------
# Ferramenta
# ---------------------------------------------------------------------------

class GenerateImageTool(BaseTool):
    name = "generate_image"
    accepts_context = True
    description = (
        "Gera uma imagem a partir de uma descrição detalhada e a mostra ao usuário. Use quando ele pedir uma "
        "imagem, ilustração, desenho, logotipo ou arte. Para editar ou se inspirar em imagens já enviadas, passe "
        "os ids delas em reference_image_ids (os ids aparecem nos rótulos das imagens)."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "prompt": {
                "type": "string",
                "description": "Descrição detalhada: assunto, estilo, composição, cores, iluminação e textos que devem aparecer.",
            },
            "aspect_ratio": {
                "type": "string",
                "enum": list(ASPECT_RATIOS),
                "description": "Proporção da imagem. Padrão: 1:1.",
            },
            "reference_image_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Ids de imagens da conversa para editar ou usar como referência (até 3).",
            },
        },
        "required": ["prompt"],
    }

    async def _run_async_impl(self, tool_call_id: str = "", **kwargs: Any) -> ToolResult:
        context: ToolContext = kwargs.pop("_context", None) or ToolContext()

        def fail(message: str) -> ToolResult:
            return ToolResult(tool_call_id=tool_call_id, name=self.name, status="error", error=message)

        prompt = str(kwargs.get("prompt") or "").strip()
        if not prompt:
            return fail("Descreva a imagem em 'prompt'.")
        if len(prompt) > 4000:
            prompt = prompt[:4000]
        aspect = str(kwargs.get("aspect_ratio") or "1:1")
        if aspect not in ASPECT_RATIOS:
            aspect = "1:1"

        backend = resolve_image_backend(context)
        if backend is None:
            return fail(
                "Geração de imagens indisponível: configure uma chave da OpenAI, do Google Gemini ou do "
                "OpenRouter (na interface, em Conexão)."
            )
        backend_name, key = backend

        refs: List[Reference] = []
        ids = kwargs.get("reference_image_ids") or []
        if isinstance(ids, str):
            ids = [ids]
        for ref_id in list(ids)[:MAX_REFERENCES]:
            att = get_attachment(str(ref_id).strip(), owner=context.owner)
            data = read_image_bytes(att) if att and att.kind == "image" else None
            if data is None:
                return fail(f"Imagem de referência não encontrada: {ref_id}")
            refs.append((att.mime_type, data))

        try:
            image_bytes, model = await _BACKENDS[backend_name](prompt, aspect, refs, key)
            att = save_image_bytes(image_bytes, f"{_slug(prompt)}.png", source="generated", owner=context.owner)
        except (ProviderError, AttachmentError) as exc:
            message = str(exc)
            if key and len(key) >= 6:
                message = message.replace(key, "***")
            return fail(f"Não foi possível gerar a imagem: {message}")

        return ToolResult(
            tool_call_id=tool_call_id,
            name=self.name,
            status="success",
            output={
                "message": "Imagem gerada e exibida ao usuário.",
                "image_id": att.id,
                "backend": backend_name,
                "model": model,
                "aspect_ratio": aspect,
            },
            attachments=[att.to_public()],
        )

    def _run_sync(self, tool_call_id: str = "", **kwargs: Any) -> ToolResult:
        import asyncio

        return asyncio.run(self._run_async_impl(tool_call_id, **kwargs))
