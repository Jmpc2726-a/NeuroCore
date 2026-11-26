"""
Modelos de dados compartilhados pelo agente e pelos provedores.
"""

from __future__ import annotations

import re
import time
import uuid
from abc import ABC, abstractmethod
from typing import Any, AsyncIterator, Dict, List, Optional, Union

from pydantic import BaseModel, Field

from neurocore.attachments import Attachment


class ToolCall(BaseModel):
    id: str = Field(default_factory=lambda: f"call_{uuid.uuid4().hex[:12]}")
    name: str
    arguments: Dict[str, Any] = Field(default_factory=dict)


class ToolDefinition(BaseModel):
    name: str
    description: str
    parameters: Dict[str, Any] = Field(default_factory=dict)


class Message(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:16])
    role: str  # "system" | "user" | "assistant" | "tool"
    content: Optional[str] = None
    name: Optional[str] = None
    tool_call_id: Optional[str] = None
    tool_calls: Optional[List[ToolCall]] = None
    is_error: bool = False
    # Imagens e documentos: enviados pelo usuário (mensagens "user") ou
    # produzidos por ferramentas (mensagens "tool").
    attachments: Optional[List[Attachment]] = None
    created_at: float = Field(default_factory=time.time)
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


def tool_dict(t: Union[ToolDefinition, Dict[str, Any]]) -> Dict[str, Any]:
    if isinstance(t, ToolDefinition):
        return {"name": t.name, "description": t.description, "parameters": t.parameters}
    return {
        "name": t.get("name", ""),
        "description": t.get("description", ""),
        "parameters": t.get("parameters", {}) or {"type": "object", "properties": {}},
    }


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
