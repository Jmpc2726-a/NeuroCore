"""
Multi-provider LLM Routing and Fallback Engine for NeuroCore Agent.

Cada provedor fala com a API HTTP do serviço diretamente (httpx), convertendo
o histórico interno (Message/ToolCall, com imagens e documentos anexados)
para o formato que a API espera.
"""

from __future__ import annotations

import json
import re
import uuid
from typing import Any, Dict, List, Optional, Set, Tuple, Union

from neurocore.agent.offline import OfflineSimulatedProvider
from neurocore.agent.schema import (
    BaseLLMProvider,
    Message,
    ModelResponse,
    ProviderConfig,
    StreamEvent,
    ToolCall,
    ToolDefinition,
    chunk_text,
    tool_dict,
)
from neurocore.attachments import Attachment, read_document_text, read_image_base64
from neurocore.config import get_settings
from neurocore.http_utils import ProviderError, post_json

__all__ = [
    "AnthropicProvider", "BaseLLMProvider", "GeminiProvider", "Message", "ModelResponse",
    "OfflineSimulatedProvider", "OpenAIProvider", "OpenRouterProvider", "ProviderConfig",
    "ProviderError", "ProviderManager", "StreamEvent", "ToolCall", "ToolDefinition", "chunk_text",
]

# Compatibilidade com o nome antigo (privado) usado em versões anteriores.
_tool_dict = tool_dict


# ---------------------------------------------------------------------------
# Anexos (imagens e documentos) -> partes neutras de conteúdo
# ---------------------------------------------------------------------------

Part = Tuple[str, Any]  # ("text", str) | ("image", (mime, base64))

_SOURCE_LABELS = {
    "upload": "Imagem anexada pelo usuário",
    "chart": "Gráfico gerado pela ferramenta create_chart",
    "generated": "Imagem gerada pela ferramenta generate_image",
    "enem": "Figura da questão do ENEM",
}


def select_image_ids(messages: List[Message], limit: Optional[int] = None) -> Set[str]:
    """Escolhe as imagens mais recentes que cabem no limite por chamada."""
    limit = get_settings().MAX_IMAGES_PER_REQUEST if limit is None else limit
    chosen: List[str] = []
    for msg in reversed(messages):
        for att in reversed(msg.attachments or []):
            if att.kind != "image" or att.id in chosen:
                continue
            if len(chosen) >= limit:
                return set(chosen)
            chosen.append(att.id)
    return set(chosen)


def _image_label(att: Attachment) -> str:
    label = _SOURCE_LABELS.get(att.source, "Imagem")
    size = f", {att.width}x{att.height}px" if att.width and att.height else ""
    return f'[{label}: "{att.name}"{size} · id={att.id}]'


def _document_block(att: Attachment) -> str:
    text = read_document_text(att)
    name = att.name.replace('"', "'")
    pages = f' páginas="{att.pages}"' if att.pages else ""
    if not text:
        return f'[Documento "{name}" não está mais disponível no servidor.]'
    return f'<documento nome="{name}" tipo="{att.mime_type}"{pages}>\n{text}\n</documento>'


def attachment_parts(attachments: Optional[List[Attachment]], image_ids: Set[str]) -> List[Part]:
    parts: List[Part] = []
    for att in attachments or []:
        if att.kind == "image":
            data = read_image_base64(att) if att.id in image_ids else None
            if data:
                parts.append(("text", _image_label(att)))
                parts.append(("image", (att.mime_type, data)))
            elif att.id in image_ids:
                parts.append(("text", _image_label(att) + " (arquivo não está mais disponível)"))
            else:
                parts.append(("text", _image_label(att) + " (imagem anterior, não reenviada para economizar contexto)"))
        elif att.kind == "document":
            parts.append(("text", _document_block(att)))
    return parts


# ---------------------------------------------------------------------------
# Google Gemini
# ---------------------------------------------------------------------------

def _gemini_parts(parts: List[Part]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for kind, value in parts:
        if kind == "image":
            mime, data = value
            out.append({"inlineData": {"mimeType": mime, "data": data}})
        else:
            out.append({"text": value})
    return out


class GeminiProvider(BaseLLMProvider):
    name = "gemini"

    def __init__(self, api_key: Optional[str] = None):
        key = api_key or get_settings().gemini_api_key
        super().__init__(api_key=key, default_model=get_settings().default_model_gemini)

    def is_available(self) -> bool:
        return bool(self.api_key and self.api_key.strip())

    @staticmethod
    def _build_contents(messages: List[Message]) -> tuple[str, List[Dict[str, Any]]]:
        system_parts: List[str] = []
        contents: List[Dict[str, Any]] = []
        image_ids = select_image_ids(messages)
        pending_images: List[Dict[str, Any]] = []

        def push(role: str, parts: List[Dict[str, Any]]) -> None:
            if not parts:
                return
            if contents and contents[-1]["role"] == role:
                contents[-1]["parts"].extend(parts)
            else:
                contents.append({"role": role, "parts": list(parts)})

        def flush_images() -> None:
            # Imagens devolvidas por ferramentas vão depois das respostas das funções.
            if pending_images:
                push("user", pending_images[:])
                pending_images.clear()

        for msg in messages:
            if msg.role != "tool":
                flush_images()
            if msg.role == "system":
                if msg.content:
                    system_parts.append(msg.content)
            elif msg.role == "assistant":
                if msg.raw_provider == "gemini" and isinstance(msg.raw_content, list):
                    push("model", list(msg.raw_content))
                    continue
                parts: List[Dict[str, Any]] = []
                if msg.content:
                    parts.append({"text": msg.content})
                for tc in msg.tool_calls or []:
                    parts.append({"functionCall": {"name": tc.name, "args": tc.arguments}})
                push("model", parts)
            elif msg.role == "tool":
                push("user", [{
                    "functionResponse": {
                        "name": msg.name or "tool",
                        "response": {"error" if msg.is_error else "result": msg.content or ""},
                    }
                }])
                pending_images.extend(_gemini_parts(attachment_parts(msg.attachments, image_ids)))
            else:
                parts = _gemini_parts(attachment_parts(msg.attachments, image_ids))
                if msg.content:
                    parts.append({"text": msg.content})
                push("user", parts)
        flush_images()
        return "\n\n".join(system_parts), contents

    async def generate(
        self,
        messages: List[Message],
        tools: List[Union[ToolDefinition, Dict[str, Any]]],
        config: Optional[ProviderConfig] = None,
    ) -> ModelResponse:
        if not self.is_available():
            raise ProviderError("Gemini API key is unconfigured")

        model = self._model(config)
        if not re.fullmatch(r"[A-Za-z0-9._\-]+", model):
            raise ProviderError("Gemini: nome de modelo inválido")
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

        system_text, contents = self._build_contents(messages)
        payload: Dict[str, Any] = {
            "contents": contents,
            "generationConfig": {"temperature": config.temperature if config else 0.7},
        }
        if system_text:
            payload["systemInstruction"] = {"parts": [{"text": system_text}]}
        if tools:
            payload["tools"] = [{"functionDeclarations": [tool_dict(t) for t in tools]}]

        # A chave vai no cabeçalho, nunca na URL (URLs aparecem em logs e erros).
        headers = {"x-goog-api-key": self.api_key or "", "Content-Type": "application/json"}
        data = await post_json("Gemini", url, payload, headers)

        candidates = data.get("candidates", [])
        if not candidates:
            reason = (data.get("promptFeedback") or {}).get("blockReason")
            text = f"O Gemini bloqueou a resposta ({reason})." if reason else ""
            return ModelResponse(content=text, provider="gemini", model=model)

        cand = candidates[0]
        content_parts = cand.get("content", {}).get("parts", []) or []
        text_content = ""
        tool_calls: List[ToolCall] = []
        for p in content_parts:
            if "text" in p and not p.get("thought"):
                text_content += p["text"]
            if "functionCall" in p:
                fc = p["functionCall"]
                tool_calls.append(ToolCall(name=fc.get("name", ""), arguments=fc.get("args", {}) or {}))

        usage_meta = data.get("usageMetadata", {}) or {}
        return ModelResponse(
            content=text_content,
            tool_calls=tool_calls,
            finish_reason="tool_calls" if tool_calls else "stop",
            provider="gemini",
            model=model,
            usage={
                "prompt_tokens": usage_meta.get("promptTokenCount", 0),
                "completion_tokens": usage_meta.get("candidatesTokenCount", 0),
                "total_tokens": usage_meta.get("totalTokenCount", 0),
            },
            raw_content=content_parts or None,
        )


# ---------------------------------------------------------------------------
# OpenAI / OpenRouter (formato Chat Completions)
# ---------------------------------------------------------------------------

def _openai_content(parts: List[Part], text: Optional[str]) -> Any:
    if not parts:
        return text or ""
    content: List[Dict[str, Any]] = []
    for kind, value in parts:
        if kind == "image":
            mime, data = value
            content.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{data}"}})
        else:
            content.append({"type": "text", "text": value})
    if text:
        content.append({"type": "text", "text": text})
    return content


def _to_openai_messages(messages: List[Message]) -> List[Dict[str, Any]]:
    formatted: List[Dict[str, Any]] = []
    image_ids = select_image_ids(messages)
    pending: List[Part] = []

    def flush() -> None:
        # A API não aceita imagens em mensagens "tool": elas vão numa mensagem
        # do usuário logo depois dos resultados das ferramentas.
        if pending:
            formatted.append({
                "role": "user",
                "content": _openai_content(
                    [("text", "Imagens devolvidas pelas ferramentas acima:")] + pending[:], None
                ),
            })
            pending.clear()

    for msg in messages:
        if msg.role == "tool":
            formatted.append({
                "role": "tool",
                "tool_call_id": msg.tool_call_id or "call_unknown",
                "content": msg.content or "",
            })
            pending.extend(p for p in attachment_parts(msg.attachments, image_ids))
            continue
        flush()
        if msg.role == "user":
            formatted.append({"role": "user", "content": _openai_content(attachment_parts(msg.attachments, image_ids), msg.content)})
            continue
        item: Dict[str, Any] = {"role": msg.role, "content": msg.content or ""}
        if msg.role == "assistant" and msg.tool_calls:
            item["content"] = msg.content or None
            item["tool_calls"] = [
                {"id": tc.id, "type": "function", "function": {"name": tc.name, "arguments": json.dumps(tc.arguments, ensure_ascii=False)}}
                for tc in msg.tool_calls
            ]
        formatted.append(item)
    flush()
    return formatted


def _parse_openai_response(data: Dict[str, Any], provider: str, model: str) -> ModelResponse:
    choices = data.get("choices") or []
    if not choices:
        raise ProviderError(f"{provider}: resposta sem conteúdo")
    choice = choices[0]
    msg_obj = choice.get("message") or {}
    text_content = msg_obj.get("content") or ""
    if isinstance(text_content, list):  # alguns modelos devolvem partes
        text_content = "".join(p.get("text", "") for p in text_content if isinstance(p, dict))
    tool_calls: List[ToolCall] = []
    for tc in msg_obj.get("tool_calls") or []:
        fn = tc.get("function") or {}
        try:
            args = json.loads(fn.get("arguments") or "{}")
            if not isinstance(args, dict):
                args = {}
        except (ValueError, TypeError):
            args = {}
        tool_calls.append(ToolCall(id=tc.get("id") or f"call_{uuid.uuid4().hex[:12]}", name=fn.get("name", ""), arguments=args))
    finish = choice.get("finish_reason") or "stop"
    if tool_calls:
        finish = "tool_calls"
    return ModelResponse(
        content=text_content,
        tool_calls=tool_calls,
        finish_reason=finish,
        provider=provider,
        model=data.get("model") or model,
        usage=data.get("usage") or {},
    )


class OpenAIProvider(BaseLLMProvider):
    name = "openai"
    url = "https://api.openai.com/v1/chat/completions"
    label = "OpenAI"

    def __init__(self, api_key: Optional[str] = None):
        key = api_key or get_settings().openai_api_key
        super().__init__(api_key=key, default_model=get_settings().default_model_openai)

    def is_available(self) -> bool:
        return bool(self.api_key and self.api_key.strip())

    def _headers(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    async def generate(
        self,
        messages: List[Message],
        tools: List[Union[ToolDefinition, Dict[str, Any]]],
        config: Optional[ProviderConfig] = None,
    ) -> ModelResponse:
        if not self.is_available():
            raise ProviderError(f"{self.label} API key is unconfigured")

        model = self._model(config)
        payload: Dict[str, Any] = {
            "model": model,
            "messages": _to_openai_messages(messages),
            "temperature": config.temperature if config else 0.7,
        }
        if tools:
            payload["tools"] = [{"type": "function", "function": tool_dict(t)} for t in tools]

        data = await post_json(self.label, self.url, payload, self._headers())
        return _parse_openai_response(data, self.name, model)


class OpenRouterProvider(OpenAIProvider):
    name = "openrouter"
    url = "https://openrouter.ai/api/v1/chat/completions"
    label = "OpenRouter"

    def __init__(self, api_key: Optional[str] = None):
        key = api_key or get_settings().openrouter_api_key
        BaseLLMProvider.__init__(self, api_key=key, default_model=get_settings().default_model_openrouter)

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "X-Title": "NeuroCore Agent",
            "Content-Type": "application/json",
        }


# ---------------------------------------------------------------------------
# Anthropic (Messages API)
# ---------------------------------------------------------------------------

def _anthropic_blocks(parts: List[Part]) -> List[Dict[str, Any]]:
    blocks: List[Dict[str, Any]] = []
    for kind, value in parts:
        if kind == "image":
            mime, data = value
            blocks.append({"type": "image", "source": {"type": "base64", "media_type": mime, "data": data}})
        elif value:
            blocks.append({"type": "text", "text": value})
    return blocks


class AnthropicProvider(BaseLLMProvider):
    name = "anthropic"

    def __init__(self, api_key: Optional[str] = None):
        key = api_key or get_settings().anthropic_api_key
        super().__init__(api_key=key, default_model=get_settings().default_model_anthropic)

    def is_available(self) -> bool:
        return bool(self.api_key and self.api_key.strip())

    @staticmethod
    def _build_messages(messages: List[Message]) -> tuple[str, List[Dict[str, Any]]]:
        system_parts: List[str] = []
        formatted: List[Dict[str, Any]] = []
        image_ids = select_image_ids(messages)

        def push(role: str, blocks: List[Dict[str, Any]]) -> None:
            if not blocks:
                return
            if formatted and formatted[-1]["role"] == role:
                formatted[-1]["content"].extend(blocks)
            else:
                formatted.append({"role": role, "content": list(blocks)})

        for msg in messages:
            if msg.role == "system":
                if msg.content:
                    system_parts.append(msg.content)
            elif msg.role == "assistant":
                if msg.raw_provider == "anthropic" and isinstance(msg.raw_content, list):
                    # Reenvia os blocos exatamente como vieram (inclui "thinking").
                    push("assistant", list(msg.raw_content))
                    continue
                blocks: List[Dict[str, Any]] = []
                if msg.content:
                    blocks.append({"type": "text", "text": msg.content})
                for tc in msg.tool_calls or []:
                    blocks.append({"type": "tool_use", "id": tc.id, "name": tc.name, "input": tc.arguments})
                push("assistant", blocks)
            elif msg.role == "tool":
                extra = _anthropic_blocks(attachment_parts(msg.attachments, image_ids))
                text = msg.content or "(sem saída)"
                block: Dict[str, Any] = {
                    "type": "tool_result",
                    "tool_use_id": msg.tool_call_id or "call_unknown",
                    "content": ([{"type": "text", "text": text}] + extra) if extra else text,
                }
                if msg.is_error:
                    block["is_error"] = True
                push("user", [block])
            else:
                blocks = _anthropic_blocks(attachment_parts(msg.attachments, image_ids))
                if msg.content:
                    blocks.append({"type": "text", "text": msg.content})
                push("user", blocks)

        # A primeira mensagem precisa ser do usuário.
        while formatted and formatted[0]["role"] != "user":
            formatted.pop(0)
        return "\n\n".join(system_parts), formatted

    async def generate(
        self,
        messages: List[Message],
        tools: List[Union[ToolDefinition, Dict[str, Any]]],
        config: Optional[ProviderConfig] = None,
    ) -> ModelResponse:
        if not self.is_available():
            raise ProviderError("Anthropic API key is unconfigured")

        model = self._model(config)
        system_text, formatted_msgs = self._build_messages(messages)

        # Os modelos atuais da Claude rejeitam "temperature"; o raciocínio
        # (thinking adaptativo) já vem ligado por padrão.
        payload: Dict[str, Any] = {
            "model": model,
            "messages": formatted_msgs,
            "max_tokens": config.max_tokens if config else 16000,
        }
        if system_text:
            payload["system"] = system_text
        if tools:
            payload["tools"] = [
                {"name": d["name"], "description": d["description"], "input_schema": d["parameters"]}
                for d in (tool_dict(t) for t in tools)
            ]

        headers = {
            "x-api-key": self.api_key or "",
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        data = await post_json("Anthropic", "https://api.anthropic.com/v1/messages", payload, headers)

        text_content = ""
        tool_calls: List[ToolCall] = []
        blocks = data.get("content", []) or []
        for block in blocks:
            if block.get("type") == "text":
                text_content += block.get("text", "")
            elif block.get("type") == "tool_use":
                tool_calls.append(
                    ToolCall(
                        id=block.get("id") or f"call_{uuid.uuid4().hex[:12]}",
                        name=block.get("name", ""),
                        arguments=block.get("input", {}) or {},
                    )
                )

        stop_reason = data.get("stop_reason")
        finish = "tool_calls" if tool_calls else "stop"
        if stop_reason == "refusal":
            finish = "refusal"
            if not text_content:
                text_content = "O modelo recusou esta solicitação por motivos de segurança."
            tool_calls = []
        elif stop_reason == "max_tokens" and not tool_calls:
            finish = "length"

        usage = data.get("usage", {}) or {}
        return ModelResponse(
            content=text_content,
            tool_calls=tool_calls,
            finish_reason=finish,
            provider="anthropic",
            model=data.get("model") or model,
            usage={
                "prompt_tokens": usage.get("input_tokens", 0),
                "completion_tokens": usage.get("output_tokens", 0),
                "total_tokens": usage.get("input_tokens", 0) + usage.get("output_tokens", 0),
            },
            raw_content=blocks if finish != "refusal" else None,
        )


# ---------------------------------------------------------------------------
# Gerenciador e cascata de fallback
# ---------------------------------------------------------------------------

PROVIDER_ALIASES = {"claude": "anthropic", "offline": "local", "simulated": "local"}


def _sanitize_error(exc: Exception, secrets: List[Optional[str]]) -> str:
    text = str(exc) if isinstance(exc, ProviderError) else f"{type(exc).__name__}: {exc}"
    for secret in secrets:
        if secret and len(secret) >= 6:
            text = text.replace(secret, "***")
    return text[:400]


class ProviderManager:
    CASCADE_PRIORITY = ["gemini", "openai", "anthropic", "openrouter", "local"]
    PROVIDER_CLASSES = {
        "gemini": GeminiProvider,
        "openai": OpenAIProvider,
        "anthropic": AnthropicProvider,
        "openrouter": OpenRouterProvider,
    }

    def __init__(self, config_provider: Optional[str] = None):
        self.providers: Dict[str, BaseLLMProvider] = {
            "gemini": GeminiProvider(),
            "openai": OpenAIProvider(),
            "anthropic": AnthropicProvider(),
            "openrouter": OpenRouterProvider(),
            "local": OfflineSimulatedProvider(),
        }
        self.providers["claude"] = self.providers["anthropic"]
        self.providers["offline"] = self.providers["local"]
        self.default_provider = config_provider or get_settings().default_provider

    @staticmethod
    def normalize(name: Optional[str]) -> str:
        key = (name or "").lower().strip()
        return PROVIDER_ALIASES.get(key, key)

    def get_provider(self, name: str) -> BaseLLMProvider:
        return self.providers.get(self.normalize(name), self.providers["local"])

    def get_available_providers(self) -> Dict[str, Dict[str, Any]]:
        settings = get_settings()
        result = {}
        for p_name in ["local", "gemini", "openai", "anthropic", "openrouter"]:
            provider = self.get_provider(p_name)
            result[p_name] = {
                "available": provider.is_available(),
                "model": provider.default_model,
                "configured": settings.is_provider_configured(p_name),
            }
        return result

    async def generate_with_fallback(
        self,
        messages: List[Message],
        tools: List[Union[ToolDefinition, Dict[str, Any]]],
        requested_provider: Optional[str] = None,
        model_override: Optional[str] = None,
        temperature: float = 0.7,
        api_key: Optional[str] = None,
    ) -> ModelResponse:
        primary = self.normalize(requested_provider or self.default_provider)
        if primary not in self.CASCADE_PRIORITY:
            primary = "local"
        api_key = (api_key or "").strip() or None

        cascade = [primary] + [p for p in self.CASCADE_PRIORITY if p != primary]
        fallback_trace: List[str] = []

        for p_name in cascade:
            if p_name == primary and api_key and p_name in self.PROVIDER_CLASSES:
                # Instância exclusiva desta requisição: a chave do usuário nunca
                # fica gravada no provedor compartilhado por todos os visitantes.
                provider: BaseLLMProvider = self.PROVIDER_CLASSES[p_name](api_key=api_key)
            else:
                provider = self.get_provider(p_name)

            if not provider.is_available() and p_name != "local":
                # Só registra a ausência de chave do provedor pedido; os demais
                # sem chave são pulados em silêncio.
                if p_name == primary:
                    fallback_trace.append(f"{p_name}: API key missing or unconfigured")
                continue

            try:
                # O modelo escolhido só vale para o provedor escolhido; no
                # fallback cada provedor usa o seu modelo padrão.
                model = model_override if (p_name == primary and model_override) else provider.default_model
                cfg = ProviderConfig(provider_name=p_name, model=model, temperature=temperature)
                response = await provider.generate(messages, tools, cfg)
                response.fallback_occurred = p_name != primary or bool(fallback_trace)
                response.fallback_trace = fallback_trace
                return response
            except Exception as exc:
                fallback_trace.append(f"{p_name}: Failed with error '{_sanitize_error(exc, [api_key, provider.api_key])}'")

        local_provider = self.providers["local"]
        cfg = ProviderConfig(provider_name="local", model=local_provider.default_model, temperature=temperature)
        resp = await local_provider.generate(messages, tools, cfg)
        resp.fallback_occurred = True
        resp.fallback_trace = fallback_trace
        return resp
