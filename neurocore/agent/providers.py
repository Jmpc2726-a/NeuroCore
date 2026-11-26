"""
Multi-provider LLM Routing and Fallback Engine for NeuroCore Agent.

Cada provedor fala com a API HTTP do serviço diretamente (httpx), convertendo
o histórico interno (Message/ToolCall) para o formato que a API espera.
"""

from __future__ import annotations

import json
import re
import uuid
import asyncio
from abc import ABC, abstractmethod
from typing import Any, AsyncIterator, Dict, List, Optional, Union

import httpx
from pydantic import BaseModel, Field

from neurocore.config import get_settings


class ToolCall(BaseModel):
    id: str = Field(default_factory=lambda: f"call_{uuid.uuid4().hex[:12]}")
    name: str
    arguments: Dict[str, Any] = Field(default_factory=dict)


class ToolDefinition(BaseModel):
    name: str
    description: str
    parameters: Dict[str, Any] = Field(default_factory=dict)


class Message(BaseModel):
    role: str  # "system" | "user" | "assistant" | "tool"
    content: Optional[str] = None
    name: Optional[str] = None
    tool_call_id: Optional[str] = None
    tool_calls: Optional[List[ToolCall]] = None
    is_error: bool = False
    # Conteúdo bruto devolvido pelo provedor (ex.: blocos de "thinking" da
    # Anthropic ou assinaturas do Gemini). É reenviado sem alterações quando a
    # próxima chamada vai para o mesmo provedor.
    raw_provider: Optional[str] = None
    raw_content: Optional[Any] = None


class ProviderConfig(BaseModel):
    provider_name: str
    model: str
    temperature: float = 0.7
    max_tokens: int = 16000
    api_key: Optional[str] = None


class ModelResponse(BaseModel):
    content: Optional[str] = None
    tool_calls: List[ToolCall] = Field(default_factory=list)
    finish_reason: str = "stop"  # "stop" | "tool_calls" | "length" | "error" | "refusal"
    provider: str
    model: str
    usage: Dict[str, Any] = Field(default_factory=lambda: {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0})
    fallback_occurred: bool = False
    fallback_trace: List[str] = Field(default_factory=list)
    raw_content: Optional[Any] = None


class StreamEvent(BaseModel):
    event_type: str  # "connected" | "token" | "tool_start" | "tool_end" | "diff" | "fallback" | "done" | "error"
    data: Dict[str, Any] = Field(default_factory=dict)


class ProviderError(RuntimeError):
    """Erro de um provedor, já sem dados sensíveis (chaves, URLs com token)."""


def _timeout() -> httpx.Timeout:
    return httpx.Timeout(float(get_settings().LLM_TIMEOUT), connect=15.0)


def _tool_dict(t: Union[ToolDefinition, Dict[str, Any]]) -> Dict[str, Any]:
    if isinstance(t, ToolDefinition):
        return {"name": t.name, "description": t.description, "parameters": t.parameters}
    return {
        "name": t.get("name", ""),
        "description": t.get("description", ""),
        "parameters": t.get("parameters", {}) or {"type": "object", "properties": {}},
    }


def _api_error(provider: str, resp: httpx.Response) -> ProviderError:
    detail = ""
    try:
        data = resp.json()
        err = data.get("error", data) if isinstance(data, dict) else data
        if isinstance(err, dict):
            detail = str(err.get("message") or err.get("type") or "")
        else:
            detail = str(err)
    except Exception:
        detail = resp.text[:200]
    return ProviderError(f"{provider} API error ({resp.status_code}): {detail[:300]}")


async def _post_json(provider: str, url: str, payload: Dict[str, Any], headers: Dict[str, str]) -> Dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=_timeout()) as client:
            resp = await client.post(url, json=payload, headers=headers)
    except httpx.TimeoutException:
        raise ProviderError(f"{provider}: tempo de resposta esgotado")
    except httpx.HTTPError as exc:
        # Não repassa str(exc): pode conter a URL da requisição.
        raise ProviderError(f"{provider}: falha de conexão ({type(exc).__name__})")
    if resp.status_code != 200:
        raise _api_error(provider, resp)
    try:
        return resp.json()
    except ValueError:
        raise ProviderError(f"{provider}: resposta inválida da API")


class BaseLLMProvider(ABC):
    name: str = "base"

    def __init__(self, api_key: Optional[str] = None, default_model: str = ""):
        self.api_key = api_key
        self.default_model = default_model

    @abstractmethod
    def is_available(self) -> bool:
        pass

    @abstractmethod
    async def generate(
        self,
        messages: List[Message],
        tools: List[Union[ToolDefinition, Dict[str, Any]]],
        config: Optional[ProviderConfig] = None,
    ) -> ModelResponse:
        pass

    async def generate_stream(
        self,
        messages: List[Message],
        tools: List[Union[ToolDefinition, Dict[str, Any]]],
        config: Optional[ProviderConfig] = None,
    ) -> AsyncIterator[StreamEvent]:
        resp = await self.generate(messages, tools, config)
        if resp.tool_calls:
            for tc in resp.tool_calls:
                yield StreamEvent(event_type="tool_start", data={"tool_call_id": tc.id, "name": tc.name, "args": tc.arguments})
        elif resp.content:
            for chunk in chunk_text(resp.content):
                yield StreamEvent(event_type="token", data={"token": chunk})

    def _model(self, config: Optional[ProviderConfig]) -> str:
        return (config.model if config and config.model else None) or self.default_model


def chunk_text(text: str, size: int = 24) -> List[str]:
    """Divide o texto em pedaços pequenos para o streaming (sem cortar palavras)."""
    chunks: List[str] = []
    buf = ""
    for piece in re.split(r"(\s+)", text):
        buf += piece
        if len(buf) >= size:
            chunks.append(buf)
            buf = ""
    if buf:
        chunks.append(buf)
    return chunks


# ---------------------------------------------------------------------------
# Google Gemini
# ---------------------------------------------------------------------------

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

        def push(role: str, parts: List[Dict[str, Any]]) -> None:
            if not parts:
                return
            if contents and contents[-1]["role"] == role:
                contents[-1]["parts"].extend(parts)
            else:
                contents.append({"role": role, "parts": parts})

        for msg in messages:
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
            else:
                if msg.content:
                    push("user", [{"text": msg.content}])
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
            payload["tools"] = [{"functionDeclarations": [_tool_dict(t) for t in tools]}]

        # A chave vai no cabeçalho, nunca na URL (URLs aparecem em logs e erros).
        headers = {"x-goog-api-key": self.api_key or "", "Content-Type": "application/json"}
        data = await _post_json("Gemini", url, payload, headers)

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

def _to_openai_messages(messages: List[Message]) -> List[Dict[str, Any]]:
    formatted: List[Dict[str, Any]] = []
    for msg in messages:
        if msg.role == "tool":
            formatted.append({
                "role": "tool",
                "tool_call_id": msg.tool_call_id or "call_unknown",
                "content": msg.content or "",
            })
            continue
        item: Dict[str, Any] = {"role": msg.role, "content": msg.content or ""}
        if msg.role == "assistant" and msg.tool_calls:
            item["content"] = msg.content or None
            item["tool_calls"] = [
                {"id": tc.id, "type": "function", "function": {"name": tc.name, "arguments": json.dumps(tc.arguments, ensure_ascii=False)}}
                for tc in msg.tool_calls
            ]
        formatted.append(item)
    return formatted


def _parse_openai_response(data: Dict[str, Any], provider: str, model: str) -> ModelResponse:
    choices = data.get("choices") or []
    if not choices:
        raise ProviderError(f"{provider}: resposta sem conteúdo")
    choice = choices[0]
    msg_obj = choice.get("message") or {}
    text_content = msg_obj.get("content") or ""
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
            payload["tools"] = [{"type": "function", "function": _tool_dict(t)} for t in tools]

        data = await _post_json(self.label, self.url, payload, self._headers())
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
                block: Dict[str, Any] = {
                    "type": "tool_result",
                    "tool_use_id": msg.tool_call_id or "call_unknown",
                    "content": msg.content or "",
                }
                if msg.is_error:
                    block["is_error"] = True
                push("user", [block])
            else:
                if msg.content:
                    push("user", [{"type": "text", "text": msg.content}])

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
                for d in (_tool_dict(t) for t in tools)
            ]

        headers = {
            "x-api-key": self.api_key or "",
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        data = await _post_json("Anthropic", "https://api.anthropic.com/v1/messages", payload, headers)

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
# Modo local (simulado, sem rede)
# ---------------------------------------------------------------------------

_PATH_RE = r"['\"]?([\w\/\.\-]+)['\"]?"
_FILE_TOKEN_RE = re.compile(r"[\w\-\/\.]*[\w\-]\.[A-Za-z0-9]{1,8}\b")


class OfflineSimulatedProvider(BaseLLMProvider):
    """
    Provedor simulado: reconhece alguns pedidos simples (em português ou
    inglês) e aciona as ferramentas correspondentes. Não é um modelo de
    linguagem: serve para testar a interface sem chave de API.
    """

    name = "local"

    def __init__(self):
        super().__init__(api_key="local_simulated", default_model=get_settings().default_model_local)

    def is_available(self) -> bool:
        return True

    @staticmethod
    def _tool_names(tools: List[Union[ToolDefinition, Dict[str, Any]]]) -> Optional[set]:
        if not tools:
            return None  # sem lista = sem restrição
        return {_tool_dict(t)["name"] for t in tools}

    @staticmethod
    def _detect_intent(user_text: str) -> List[ToolCall]:
        user_lower = user_text.lower()

        # Português -------------------------------------------------------
        if re.search(r"\b(leia|ler|leio|mostre|mostrar|abra|abrir|exiba|exibir)\b", user_lower) and \
                not re.search(r"\b(crie|criar|escreva|escrever)\b", user_lower):
            file_match = _FILE_TOKEN_RE.search(user_text)
            if file_match:
                return [ToolCall(name="read_file", arguments={"path": file_match.group(0)})]
        if re.search(r"\b(liste|listar|listagem)\b", user_lower):
            dir_match = re.search(r"\b(?:pasta|diret[óo]rio|em)\s+['\"]?([\w\/\.\-]+)['\"]?", user_text, re.IGNORECASE)
            return [ToolCall(name="list_dir", arguments={"path": dir_match.group(1) if dir_match else "."})]
        if re.search(r"\b(procure|procurar|busque|buscar|pesquise|pesquisar|encontre|encontrar)\b", user_lower):
            ident = re.search(r"\b(?:fun[çc][ãa]o|function|def|classe|class|vari[áa]vel|termo|palavra)\s+['\"`]?([A-Za-z_][\w\.]*)", user_text, re.IGNORECASE)
            if not ident:
                ident = re.search(r"['\"`]([^'\"`]+)['\"`]", user_text) or re.search(r"\b([A-Za-z]+_[\w]+)\b", user_text)
            if ident:
                return [ToolCall(name="grep_search", arguments={"pattern": ident.group(1), "path": "."})]
        if re.search(r"\b(crie|criar|escreva|escrever|gere|gerar)\b", user_lower):
            file_match = _FILE_TOKEN_RE.search(user_text)
            if file_match:
                content_match = re.search(r"(?:com o conte[úu]do|conte[úu]do)\s+['\"](.*?)['\"]", user_text, re.IGNORECASE)
                content = content_match.group(1) if content_match else "# Arquivo criado pelo NeuroCore (modo local)\n"
                return [ToolCall(name="write_file", arguments={"path": file_match.group(0), "content": content})]
        if "pytest" in user_lower and re.search(r"\b(execute|executar|rode|rodar|teste|testes)\b", user_lower):
            return [ToolCall(name="run_command", arguments={"command": "python -m pytest -q"})]
        pt_cmd = re.search(r"\b(?:execute|executar|rode|rodar)\s+(?:o\s+)?(?:comando\s+)?['\"`](.+?)['\"`]", user_text, re.IGNORECASE)
        if pt_cmd:
            return [ToolCall(name="run_command", arguments={"command": pt_cmd.group(1)})]

        # Inglês ----------------------------------------------------------
        match_read = re.search(r"(?:read|view|show|check|cat)\s+(?:file\s+)?" + _PATH_RE, user_text, re.IGNORECASE)
        if match_read and "write" not in user_lower and "create" not in user_lower:
            return [ToolCall(name="read_file", arguments={"path": match_read.group(1).strip("'\"")})]

        if re.search(r"\b(?:list|ls|dir|directory)\b", user_text, re.IGNORECASE):
            match_dir = re.search(r"(?:in|path|dir|directory)\s+" + _PATH_RE, user_text, re.IGNORECASE)
            dir_path = match_dir.group(1).strip("'\"") if match_dir else "."
            return [ToolCall(name="list_dir", arguments={"path": dir_path})]

        match_grep = re.search(r"(?:grep|search|find)\s+(?:for\s+)?['\"]?([\w\.\-]+)['\"]?", user_text, re.IGNORECASE)
        if match_grep:
            return [ToolCall(name="grep_search", arguments={"pattern": match_grep.group(1).strip("'\""), "path": "."})]

        match_write = re.search(r"(?:create|write|make)\s+(?:file\s+)?" + _PATH_RE, user_text, re.IGNORECASE)
        if match_write:
            match_content = re.search(r"with\s+(?:content\s+)?['\"](.*?)['\"]", user_text, re.IGNORECASE)
            content = match_content.group(1) if match_content else "# Generated content\n"
            return [ToolCall(name="write_file", arguments={"path": match_write.group(1).strip("'\""), "content": content})]

        match_cmd = re.search(r"(?:run|exec|execute|pytest|command)\s+(.*)", user_text, re.IGNORECASE)
        if match_cmd:
            cmd = match_cmd.group(1).strip().strip("'\"")
            if cmd.lower().startswith("command "):
                cmd = cmd[8:].strip("'\"")
            if cmd:
                return [ToolCall(name="run_command", arguments={"command": cmd})]
        return []

    async def generate(
        self,
        messages: List[Message],
        tools: List[Union[ToolDefinition, Dict[str, Any]]],
        config: Optional[ProviderConfig] = None,
    ) -> ModelResponse:
        model = self._model(config)

        if not messages:
            return ModelResponse(content="Olá! Sou o NeuroCore em modo local (simulado).", provider="local", model=model)

        last_msg = messages[-1]

        # Caso 1: resumo do resultado de uma ferramenta
        if last_msg.role == "tool":
            tool_name = last_msg.name or "ferramenta"
            tool_output = last_msg.content or ""
            if last_msg.is_error:
                summary = f"A ferramenta `{tool_name}` falhou:\n{tool_output[:500]}"
            else:
                snippet = tool_output[:1500]
                more = "\n..." if len(tool_output) > 1500 else ""
                summary = (
                    f"Operation completed successfully (`{tool_name}`). Output snippet:\n"
                    f"```\n{snippet}{more}\n```"
                )
            return ModelResponse(content=summary, provider="local", model=model)

        # Caso 2: reconhecimento de intenção a partir do pedido do usuário
        user_text = ""
        for m in reversed(messages):
            if m.role == "user" and m.content:
                user_text = m.content
                break
        user_lower = user_text.lower()

        tool_calls = self._detect_intent(user_text)
        is_greeting = re.search(r"\b(hello|hi|hey|ol[áa]|oi|bom dia|boa tarde|boa noite)\b", user_lower)
        if not tool_calls and is_greeting and len(user_lower.split()) <= 6:
            return ModelResponse(
                content=(
                    "Hello! I am NeuroCore Code Agent in local simulated fallback mode.\n\n"
                    "Olá! Estou no modo local (simulado). Posso ler, listar e buscar arquivos do "
                    "workspace. Para conversar com uma IA de verdade, configure um provedor e uma "
                    "chave de API no botão de conexão."
                ),
                provider="local",
                model=model,
            )

        allowed = self._tool_names(tools)
        if allowed is not None:
            blocked = [tc for tc in tool_calls if tc.name not in allowed]
            tool_calls = [tc for tc in tool_calls if tc.name in allowed]
            if blocked and not tool_calls:
                return ModelResponse(
                    content=(
                        f"A ferramenta `{blocked[0].name}` está desativada neste servidor por segurança. "
                        "Ative-a com NEUROCORE_ENABLE_DANGEROUS_TOOLS=true em uma instalação local."
                    ),
                    provider="local",
                    model=model,
                )

        if tool_calls:
            return ModelResponse(content=None, tool_calls=tool_calls, finish_reason="tool_calls", provider="local", model=model)

        default_reply = (
            "NeuroCore (modo local simulado): não entendi um pedido de ferramenta nesta mensagem.\n\n"
            "Neste modo eu reconheço pedidos como:\n"
            "- `Leia o arquivo README.md`\n"
            "- `Liste os arquivos da pasta neurocore`\n"
            "- `Procure a função run_stream`\n"
            "- `Execute os testes com pytest`\n\n"
            "Para respostas de verdade, escolha um provedor (Gemini, OpenAI, Anthropic ou OpenRouter) "
            "e informe sua chave de API em **Conexão**."
        )
        return ModelResponse(content=default_reply, provider="local", model=model)

    async def generate_stream(
        self,
        messages: List[Message],
        tools: List[Union[ToolDefinition, Dict[str, Any]]],
        config: Optional[ProviderConfig] = None,
    ) -> AsyncIterator[StreamEvent]:
        async for event in super().generate_stream(messages, tools, config):
            yield event
            await asyncio.sleep(0)


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
