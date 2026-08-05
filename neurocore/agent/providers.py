"""
Multi-provider LLM Routing and Fallback Engine for NeuroCore Agent.
"""

from __future__ import annotations

import json
import re
import uuid
import asyncio
from abc import ABC, abstractmethod
from typing import Any, AsyncIterator, Dict, List, Optional, Union
from pydantic import BaseModel, Field
import httpx

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


class ProviderConfig(BaseModel):
    provider_name: str
    model: str
    temperature: float = 0.7
    max_tokens: int = 4096
    api_key: Optional[str] = None


class ModelResponse(BaseModel):
    content: Optional[str] = None
    tool_calls: List[ToolCall] = Field(default_factory=list)
    finish_reason: str = "stop"  # "stop" | "tool_calls" | "length" | "error"
    provider: str
    model: str
    usage: Dict[str, Any] = Field(default_factory=lambda: {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0})
    fallback_occurred: bool = False
    fallback_trace: List[str] = Field(default_factory=list)


class StreamEvent(BaseModel):
    event_type: str  # "connected" | "token" | "tool_start" | "tool_end" | "diff" | "fallback" | "done" | "error"
    data: Dict[str, Any] = Field(default_factory=dict)


class BaseLLMProvider(ABC):
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

    @abstractmethod
    async def generate_stream(
        self,
        messages: List[Message],
        tools: List[Union[ToolDefinition, Dict[str, Any]]],
        config: Optional[ProviderConfig] = None,
    ) -> AsyncIterator[StreamEvent]:
        pass


class GeminiProvider(BaseLLMProvider):
    def __init__(self, api_key: Optional[str] = None):
        key = api_key or get_settings().gemini_api_key
        super().__init__(api_key=key, default_model=get_settings().default_model_gemini)

    def is_available(self) -> bool:
        return bool(self.api_key and self.api_key.strip())

    async def generate(
        self,
        messages: List[Message],
        tools: List[Union[ToolDefinition, Dict[str, Any]]],
        config: Optional[ProviderConfig] = None,
    ) -> ModelResponse:
        if not self.is_available():
            raise RuntimeError("Gemini API key is unconfigured")

        model = (config.model if config and getattr(config, "model", None) else None) or self.default_model
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={self.api_key}"

        contents = []
        for msg in messages:
            role = "model" if msg.role == "assistant" else "user"
            parts = []
            if msg.content:
                parts.append({"text": msg.content})
            if msg.tool_calls:
                for tc in msg.tool_calls:
                    parts.append({"functionCall": {"name": tc.name, "args": tc.arguments}})
            if msg.role == "tool" and msg.content:
                parts.append({"functionResponse": {"name": msg.name or "tool", "response": {"result": msg.content}}})
            if parts:
                contents.append({"role": role, "parts": parts})

        payload: Dict[str, Any] = {"contents": contents}

        if tools:
            func_decls = []
            for t in tools:
                if isinstance(t, ToolDefinition):
                    func_decls.append({"name": t.name, "description": t.description, "parameters": t.parameters})
                elif isinstance(t, dict):
                    func_decls.append(t)
            payload["tools"] = [{"functionDeclarations": func_decls}]

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, json=payload)
            if resp.status_code != 200:
                raise RuntimeError(f"Gemini API error ({resp.status_code}): {resp.text}")
            data = resp.json()

        candidates = data.get("candidates", [])
        if not candidates:
            return ModelResponse(content="", provider="gemini", model=model)

        cand = candidates[0]
        content_parts = cand.get("content", {}).get("parts", [])
        text_content = ""
        tool_calls: List[ToolCall] = []

        for p in content_parts:
            if "text" in p:
                text_content += p["text"]
            if "functionCall" in p:
                fc = p["functionCall"]
                tool_calls.append(ToolCall(name=fc.get("name", ""), arguments=fc.get("args", {})))

        finish_reason = "tool_calls" if tool_calls else "stop"
        return ModelResponse(
            content=text_content,
            tool_calls=tool_calls,
            finish_reason=finish_reason,
            provider="gemini",
            model=model,
            usage=data.get("usageMetadata", {}),
        )

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
            for char in resp.content:
                yield StreamEvent(event_type="token", data={"token": char})


class OpenAIProvider(BaseLLMProvider):
    def __init__(self, api_key: Optional[str] = None):
        key = api_key or get_settings().openai_api_key
        super().__init__(api_key=key, default_model=get_settings().default_model_openai)

    def is_available(self) -> bool:
        return bool(self.api_key and self.api_key.strip())

    async def generate(
        self,
        messages: List[Message],
        tools: List[Union[ToolDefinition, Dict[str, Any]]],
        config: Optional[ProviderConfig] = None,
    ) -> ModelResponse:
        if not self.is_available():
            raise RuntimeError("OpenAI API key is unconfigured")

        model = (config.model if config and getattr(config, "model", None) else None) or self.default_model
        url = "https://api.openai.com/v1/chat/completions"

        formatted_msgs = []
        for msg in messages:
            item: Dict[str, Any] = {"role": msg.role, "content": msg.content or ""}
            if msg.tool_call_id:
                item["tool_call_id"] = msg.tool_call_id
            if msg.name:
                item["name"] = msg.name
            if msg.tool_calls:
                item["tool_calls"] = [
                    {"id": tc.id, "type": "function", "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)}}
                    for tc in msg.tool_calls
                ]
            formatted_msgs.append(item)

        payload: Dict[str, Any] = {
            "model": model,
            "messages": formatted_msgs,
            "temperature": config.temperature if config else 0.7,
        }

        if tools:
            formatted_tools = []
            for t in tools:
                if isinstance(t, ToolDefinition):
                    formatted_tools.append({"type": "function", "function": {"name": t.name, "description": t.description, "parameters": t.parameters}})
                elif isinstance(t, dict):
                    formatted_tools.append({"type": "function", "function": t})
            payload["tools"] = formatted_tools

        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, json=payload, headers=headers)
            if resp.status_code != 200:
                raise RuntimeError(f"OpenAI API error ({resp.status_code}): {resp.text}")
            data = resp.json()

        choice = data["choices"][0]
        msg_obj = choice["message"]
        text_content = msg_obj.get("content") or ""
        tool_calls: List[ToolCall] = []

        if msg_obj.get("tool_calls"):
            for tc in msg_obj["tool_calls"]:
                try:
                    args = json.loads(tc["function"]["arguments"])
                except Exception:
                    args = {}
                tool_calls.append(ToolCall(id=tc.get("id", f"call_{uuid.uuid4().hex[:12]}"), name=tc["function"]["name"], arguments=args))

        finish_reason = choice.get("finish_reason", "stop")
        return ModelResponse(
            content=text_content,
            tool_calls=tool_calls,
            finish_reason=finish_reason,
            provider="openai",
            model=model,
            usage=data.get("usage", {}),
        )

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
            for char in resp.content:
                yield StreamEvent(event_type="token", data={"token": char})


class AnthropicProvider(BaseLLMProvider):
    def __init__(self, api_key: Optional[str] = None):
        key = api_key or get_settings().anthropic_api_key
        super().__init__(api_key=key, default_model=get_settings().default_model_anthropic)

    def is_available(self) -> bool:
        return bool(self.api_key and self.api_key.strip())

    async def generate(
        self,
        messages: List[Message],
        tools: List[Union[ToolDefinition, Dict[str, Any]]],
        config: Optional[ProviderConfig] = None,
    ) -> ModelResponse:
        if not self.is_available():
            raise RuntimeError("Anthropic API key is unconfigured")

        model = (config.model if config and getattr(config, "model", None) else None) or self.default_model
        url = "https://api.anthropic.com/v1/messages"

        system_str = ""
        formatted_msgs = []

        for msg in messages:
            if msg.role == "system":
                system_str += (msg.content or "") + "\n"
            else:
                role = "assistant" if msg.role == "assistant" else "user"
                formatted_msgs.append({"role": role, "content": msg.content or ""})

        payload: Dict[str, Any] = {
            "model": model,
            "messages": formatted_msgs,
            "max_tokens": config.max_tokens if config else 4096,
            "temperature": config.temperature if config else 0.7,
        }
        if system_str.strip():
            payload["system"] = system_str.strip()

        if tools:
            formatted_tools = []
            for t in tools:
                if isinstance(t, ToolDefinition):
                    formatted_tools.append({"name": t.name, "description": t.description, "input_schema": t.parameters})
                elif isinstance(t, dict):
                    formatted_tools.append({"name": t.get("name", ""), "description": t.get("description", ""), "input_schema": t.get("parameters", {})})
            payload["tools"] = formatted_tools

        headers = {
            "x-api-key": self.api_key or "",
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, json=payload, headers=headers)
            if resp.status_code != 200:
                raise RuntimeError(f"Anthropic API error ({resp.status_code}): {resp.text}")
            data = resp.json()

        text_content = ""
        tool_calls: List[ToolCall] = []

        for block in data.get("content", []):
            if block.get("type") == "text":
                text_content += block.get("text", "")
            elif block.get("type") == "tool_use":
                tool_calls.append(
                    ToolCall(
                        id=block.get("id", f"call_{uuid.uuid4().hex[:12]}"),
                        name=block.get("name", ""),
                        arguments=block.get("input", {}),
                    )
                )

        return ModelResponse(
            content=text_content,
            tool_calls=tool_calls,
            finish_reason="tool_calls" if tool_calls else "stop",
            provider="anthropic",
            model=model,
            usage=data.get("usage", {}),
        )

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
            for char in resp.content:
                yield StreamEvent(event_type="token", data={"token": char})


class OfflineSimulatedProvider(BaseLLMProvider):
    def __init__(self):
        super().__init__(api_key="local_simulated", default_model=get_settings().default_model_local)

    def is_available(self) -> bool:
        return True

    async def generate(
        self,
        messages: List[Message],
        tools: List[Union[ToolDefinition, Dict[str, Any]]],
        config: Optional[ProviderConfig] = None,
    ) -> ModelResponse:
        model = (config.model if config and getattr(config, "model", None) else None) or self.default_model

        if not messages:
            return ModelResponse(content="Hello! I am NeuroCore Code Agent in local simulated fallback mode.", provider="local", model=model)

        last_msg = messages[-1]

        # Case 1: Tool result response synthesis
        if last_msg.role == "tool":
            tool_name = last_msg.name or "tool"
            tool_output = last_msg.content or ""
            summary = f"Operation completed successfully. Output snippet:\n{tool_output[:300]}"
            return ModelResponse(
                content=summary,
                tool_calls=[],
                finish_reason="stop",
                provider="local",
                model=model,
            )

        # Case 2: Intent pattern matching from user prompt
        user_text = ""
        for m in reversed(messages):
            if m.role == "user" and m.content:
                user_text = m.content
                break

        tool_calls: List[ToolCall] = []
        user_lower = user_text.lower()

        # Intent 0: Greeting
        if ("hello" in user_lower or "hi" in user_lower) and not any(k in user_lower for k in ["read", "view", "show", "list", "grep", "search", "create", "write", "run", "exec", "pytest"]):
            return ModelResponse(
                content=f"Hello! I am NeuroCore Code Agent in local simulated fallback mode.",
                tool_calls=[],
                finish_reason="stop",
                provider="local",
                model=model,
            )

        # Intent 1: Read file
        match_read = re.search(r"(?:read|view|show|check|cat)\s+(?:file\s+)?['\"]?([\w\/\.\-]+)['\"]?", user_text, re.IGNORECASE)
        if match_read and "write" not in user_lower and "create" not in user_lower:
            path = match_read.group(1).strip("'\"")
            tool_calls.append(ToolCall(name="read_file", arguments={"path": path}))

        # Intent 2: List directory
        elif re.search(r"(?:list|ls|dir|directory)\s*(?:files|contents|folder)?", user_text, re.IGNORECASE):
            match_dir = re.search(r"(?:in|path|dir|directory)\s+['\"]?([\w\/\.\-]+)['\"]?", user_text, re.IGNORECASE)
            dir_path = match_dir.group(1).strip("'\"") if match_dir else "."
            tool_calls.append(ToolCall(name="list_dir", arguments={"path": dir_path}))

        # Intent 3: Grep search
        elif re.search(r"(?:grep|search|find)\s+(?:for\s+)?['\"]?([\w\.\-]+)['\"]?", user_text, re.IGNORECASE):
            match_grep = re.search(r"(?:grep|search|find)\s+(?:for\s+)?['\"]?([\w\.\-]+)['\"]?", user_text, re.IGNORECASE)
            pattern = match_grep.group(1).strip("'\"") if match_grep else "test"
            tool_calls.append(ToolCall(name="grep_search", arguments={"pattern": pattern, "path": "."}))

        # Intent 4: Write file
        elif re.search(r"(?:create|write|make)\s+(?:file\s+)?['\"]?([\w\/\.\-]+)['\"]?", user_text, re.IGNORECASE):
            match_write = re.search(r"(?:create|write|make)\s+(?:file\s+)?['\"]?([\w\/\.\-]+)['\"]?", user_text, re.IGNORECASE)
            path = match_write.group(1).strip("'\"") if match_write else "output.txt"
            match_content = re.search(r"with\s+(?:content\s+)?['\"](.*?)['\"]", user_text, re.IGNORECASE)
            content = match_content.group(1) if match_content else "# Generated content\n"
            tool_calls.append(ToolCall(name="write_file", arguments={"path": path, "content": content}))

        # Intent 5: Run command / pytest
        elif re.search(r"(?:run|exec|execute|pytest|command)\s+(.*)", user_text, re.IGNORECASE):
            match_cmd = re.search(r"(?:run|exec|execute|pytest|command)\s+(.*)", user_text, re.IGNORECASE)
            raw_cmd = match_cmd.group(1).strip() if match_cmd else "echo hello"
            cmd = raw_cmd.strip("'\"")
            if cmd.lower().startswith("command "):
                cmd = cmd[8:].strip("'\"")
            tool_calls.append(ToolCall(name="run_command", arguments={"command": cmd}))

        if tool_calls:
            return ModelResponse(
                content=None,
                tool_calls=tool_calls,
                finish_reason="tool_calls",
                provider="local",
                model=model,
            )

        default_reply = (
            f"NeuroCore Agent (Local Offline Mode):\n"
            f"Processed turn successfully. Request: '{user_text}'. All systems operational offline."
        )
        return ModelResponse(
            content=default_reply,
            tool_calls=[],
            finish_reason="stop",
            provider="local",
            model=model,
        )

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
            for char in resp.content:
                yield StreamEvent(event_type="token", data={"token": char})
                await asyncio.sleep(0.001)


class OpenRouterProvider(BaseLLMProvider):
    def __init__(self, api_key: Optional[str] = None):
        key = api_key or get_settings().openrouter_api_key
        super().__init__(api_key=key, default_model=get_settings().default_model_openrouter)

    def is_available(self) -> bool:
        return bool(self.api_key and self.api_key.strip())

    async def generate(
        self,
        messages: List[Message],
        tools: List[Union[ToolDefinition, Dict[str, Any]]],
        config: Optional[ProviderConfig] = None,
    ) -> ModelResponse:
        if not self.is_available():
            raise RuntimeError("OpenRouter API key is unconfigured")

        model = (config.model if config and getattr(config, "model", None) else None) or self.default_model
        url = "https://openrouter.ai/api/v1/chat/completions"

        formatted_msgs = []
        for msg in messages:
            item: Dict[str, Any] = {"role": msg.role, "content": msg.content or ""}
            if msg.tool_call_id:
                item["tool_call_id"] = msg.tool_call_id
            if msg.name:
                item["name"] = msg.name
            if msg.tool_calls:
                item["tool_calls"] = [
                    {"id": tc.id, "type": "function", "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)}}
                    for tc in msg.tool_calls
                ]
            formatted_msgs.append(item)

        payload: Dict[str, Any] = {
            "model": model,
            "messages": formatted_msgs,
            "temperature": config.temperature if config else 0.7,
        }

        if tools:
            formatted_tools = []
            for t in tools:
                if isinstance(t, ToolDefinition):
                    formatted_tools.append({"type": "function", "function": {"name": t.name, "description": t.description, "parameters": t.parameters}})
                elif isinstance(t, dict):
                    formatted_tools.append({"type": "function", "function": t})
            payload["tools"] = formatted_tools

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "HTTP-Referer": "http://localhost",
            "X-Title": "NeuroCore Agent",
            "Content-Type": "application/json"
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, json=payload, headers=headers)
            if resp.status_code != 200:
                raise RuntimeError(f"OpenRouter API error ({resp.status_code}): {resp.text}")
            data = resp.json()

        choice = data["choices"][0]
        msg_obj = choice["message"]
        text_content = msg_obj.get("content") or ""
        tool_calls: List[ToolCall] = []

        if msg_obj.get("tool_calls"):
            for tc in msg_obj["tool_calls"]:
                try:
                    args = json.loads(tc["function"]["arguments"])
                except Exception:
                    args = {}
                tool_calls.append(ToolCall(id=tc.get("id", f"call_{uuid.uuid4().hex[:12]}"), name=tc["function"]["name"], arguments=args))

        finish_reason = choice.get("finish_reason", "stop")
        return ModelResponse(
            content=text_content,
            tool_calls=tool_calls,
            finish_reason=finish_reason,
            provider="openrouter",
            model=model,
            usage=data.get("usage", {}),
        )

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
            for char in resp.content:
                yield StreamEvent(event_type="token", data={"token": char})


class ProviderManager:
    CASCADE_PRIORITY = ["gemini", "openai", "anthropic", "openrouter", "local"]

    def __init__(self, config_provider: Optional[str] = None):
        self.providers: Dict[str, BaseLLMProvider] = {
            "gemini": GeminiProvider(),
            "openai": OpenAIProvider(),
            "anthropic": AnthropicProvider(),
            "claude": AnthropicProvider(),
            "openrouter": OpenRouterProvider(),
            "local": OfflineSimulatedProvider(),
            "offline": OfflineSimulatedProvider(),
        }
        self.default_provider = config_provider or get_settings().default_provider

    def get_provider(self, name: str) -> BaseLLMProvider:
        p_key = name.lower().strip()
        return self.providers.get(p_key, self.providers["local"])

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
        primary = (requested_provider or self.default_provider).lower()
        if primary in ("claude",):
            primary = "anthropic"
        if primary in ("offline", "simulated"):
            primary = "local"

        cascade = [primary] + [p for p in self.CASCADE_PRIORITY if p != primary]
        fallback_trace: List[str] = []
        fallback_occurred = False

        for idx, p_name in enumerate(cascade):
            provider = self.get_provider(p_name)
            if api_key and p_name == primary:
                provider.api_key = api_key
            if not provider.is_available() and p_name != "local":
                fallback_trace.append(f"{p_name}: API key missing or unconfigured")
                if idx > 0 or p_name == primary:
                    fallback_occurred = True
                continue

            try:
                cfg = ProviderConfig(
                    provider_name=p_name,
                    model=model_override or provider.default_model,
                    temperature=temperature,
                )
                response = await provider.generate(messages, tools, cfg)
                response.fallback_occurred = fallback_occurred or (p_name != primary)
                response.fallback_trace = fallback_trace
                return response
            except Exception as exc:
                fallback_trace.append(f"{p_name}: Failed with error '{str(exc)}'")
                fallback_occurred = True

        local_provider = self.providers["local"]
        cfg = ProviderConfig(provider_name="local", model="simulated-local-v1", temperature=temperature)
        resp = await local_provider.generate(messages, tools, cfg)
        resp.fallback_occurred = True
        resp.fallback_trace = fallback_trace
        return resp
