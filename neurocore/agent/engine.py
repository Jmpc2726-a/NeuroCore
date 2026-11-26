from __future__ import annotations

import json
import time
import uuid
from typing import Any, AsyncIterator, Dict, List, Optional

from pydantic import BaseModel, Field

from neurocore.agent.providers import (
    Message,
    ModelResponse,
    ProviderManager,
    StreamEvent,
    ToolCall,
    ToolDefinition,
    chunk_text,
)
from neurocore.agent.memory import Session, SessionManager
from neurocore.tools.base import ToolResult
from neurocore.tools.executor import ToolExecutor

MAX_TOOL_OUTPUT_CHARS = 30000


class ChatResponse(BaseModel):
    session_id: str
    message_id: str = Field(default_factory=lambda: f"msg_{uuid.uuid4().hex[:12]}")
    content: str
    provider_used: str
    model_used: str
    tool_calls: List[Dict[str, Any]] = Field(default_factory=list)
    finish_reason: str = "stop"
    usage: Dict[str, Any] = Field(default_factory=lambda: {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0})
    execution_time_ms: int = 0
    fallback_occurred: bool = False
    fallback_trace: List[str] = Field(default_factory=list)


def tool_result_to_text(result: ToolResult) -> str:
    """Serializa a saída da ferramenta como texto (JSON quando estruturada)."""
    if result.status != "success":
        text = f"Error: {result.error or 'falha desconhecida'}"
        if isinstance(result.output, dict) and (result.output.get("stdout") or result.output.get("stderr")):
            text += "\n" + json.dumps(result.output, ensure_ascii=False)
    elif isinstance(result.output, str):
        text = result.output
    else:
        text = json.dumps(result.output, ensure_ascii=False, default=str)
    if len(text) > MAX_TOOL_OUTPUT_CHARS:
        text = text[:MAX_TOOL_OUTPUT_CHARS] + "\n... [saída truncada]"
    return text


class AgentEngine:
    def __init__(
        self,
        provider_manager: Optional[ProviderManager] = None,
        session_manager: Optional[SessionManager] = None,
        tool_executor: Optional[ToolExecutor] = None,
        max_steps: int = 15,
    ):
        self.provider_manager = provider_manager or ProviderManager()
        self.session_manager = session_manager or SessionManager()
        self.tool_executor = tool_executor or ToolExecutor()
        self.max_steps = max_steps

    def initialize(self):
        pass

    def shutdown(self):
        pass

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _start_turn(self, user_prompt: str, session_id: Optional[str], provider: Optional[str]) -> Session:
        session = self.session_manager.get_or_create_session(session_id, provider=provider or "local")
        self.session_manager.add_message(session.session_id, Message(role="user", content=user_prompt))
        return session

    async def _call_model(
        self,
        session: Session,
        tool_defs: List[Dict[str, Any]],
        provider: Optional[str],
        model: Optional[str],
        temperature: float,
        api_key: Optional[str],
    ) -> ModelResponse:
        messages_for_llm = [Message(role="system", content=session.system_prompt)] + session.messages
        return await self.provider_manager.generate_with_fallback(
            messages=messages_for_llm,
            tools=tool_defs,
            requested_provider=provider or session.provider,
            model_override=model or (session.model if not provider or provider == session.provider else None),
            temperature=temperature,
            api_key=api_key,
        )

    def _record_assistant(self, session: Session, response: ModelResponse, content: Optional[str]) -> None:
        self.session_manager.add_message(
            session.session_id,
            Message(
                role="assistant",
                content=content,
                tool_calls=response.tool_calls or None,
                raw_provider=response.provider if response.raw_content else None,
                raw_content=response.raw_content,
            ),
        )

    async def _execute_tool(self, session: Session, tc: ToolCall) -> ToolResult:
        result = await self.tool_executor.execute(tool_call_id=tc.id, name=tc.name, arguments=tc.arguments or {})
        self.session_manager.add_message(
            session.session_id,
            Message(
                role="tool",
                content=tool_result_to_text(result),
                name=tc.name,
                tool_call_id=tc.id,
                is_error=result.status != "success",
            ),
        )
        return result

    # ------------------------------------------------------------------
    # Execução síncrona (resposta única)
    # ------------------------------------------------------------------

    async def run(
        self,
        user_prompt: str,
        session_id: Optional[str] = None,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        tools_enabled: bool = True,
        temperature: float = 0.7,
        api_key: Optional[str] = None,
    ) -> ChatResponse:
        start_time = time.time()
        session = self._start_turn(user_prompt, session_id, provider)

        all_tool_traces: List[Dict[str, Any]] = []
        final_content = ""
        last_response: Optional[ModelResponse] = None
        fallback_trace: List[str] = []
        fallback_occurred = False
        tool_defs = self.tool_executor.get_tool_definitions() if tools_enabled else []
        finished = False

        for _ in range(self.max_steps):
            model_response = await self._call_model(session, tool_defs, provider, model, temperature, api_key)
            last_response = model_response
            if model_response.fallback_occurred:
                fallback_occurred = True
                for item in model_response.fallback_trace:
                    if item not in fallback_trace:
                        fallback_trace.append(item)

            if model_response.tool_calls:
                self._record_assistant(session, model_response, model_response.content or None)
                for tc in model_response.tool_calls:
                    result = await self._execute_tool(session, tc)
                    trace = result.model_dump()
                    trace["arguments"] = tc.arguments
                    all_tool_traces.append(trace)
                continue

            final_content = model_response.content or ""
            self._record_assistant(session, model_response, final_content)
            finished = True
            break

        if not finished:
            final_content = f"O agente atingiu o limite de {self.max_steps} etapas sem concluir a resposta."
            self.session_manager.add_message(session.session_id, Message(role="assistant", content=final_content))

        elapsed_ms = int((time.time() - start_time) * 1000)
        return ChatResponse(
            session_id=session.session_id,
            content=final_content,
            provider_used=last_response.provider if last_response else (provider or "local"),
            model_used=last_response.model if last_response else "unknown",
            tool_calls=all_tool_traces,
            finish_reason=last_response.finish_reason if last_response else "stop",
            usage=last_response.usage if last_response else {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            execution_time_ms=elapsed_ms,
            fallback_occurred=fallback_occurred,
            fallback_trace=fallback_trace,
        )

    # ------------------------------------------------------------------
    # Execução com streaming de eventos (SSE)
    # ------------------------------------------------------------------

    async def run_stream(
        self,
        user_prompt: str,
        session_id: Optional[str] = None,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        tools_enabled: bool = True,
        temperature: float = 0.7,
        api_key: Optional[str] = None,
    ) -> AsyncIterator[StreamEvent]:
        start_time = time.time()
        session = self._start_turn(user_prompt, session_id, provider)
        yield StreamEvent(event_type="connected", data={"session_id": session.session_id})

        tool_defs = self.tool_executor.get_tool_definitions() if tools_enabled else []
        finished = False
        last_response: Optional[ModelResponse] = None

        try:
            for _ in range(self.max_steps):
                model_response = await self._call_model(session, tool_defs, provider, model, temperature, api_key)
                last_response = model_response

                if model_response.fallback_occurred:
                    yield StreamEvent(
                        event_type="fallback",
                        data={
                            "provider_used": model_response.provider,
                            "fallback_trace": model_response.fallback_trace,
                        },
                    )

                if model_response.tool_calls:
                    if model_response.content:
                        yield StreamEvent(event_type="token", data={"token": model_response.content + "\n\n"})
                    self._record_assistant(session, model_response, model_response.content or None)
                    for tc in model_response.tool_calls:
                        yield StreamEvent(
                            event_type="tool_start",
                            data={"tool_call_id": tc.id, "name": tc.name, "args": tc.arguments},
                        )
                        result = await self._execute_tool(session, tc)
                        if isinstance(result.output, dict) and result.output.get("diff"):
                            yield StreamEvent(
                                event_type="diff",
                                data={"path": tc.arguments.get("path", ""), "diff": result.output["diff"]},
                            )
                        yield StreamEvent(
                            event_type="tool_end",
                            data={
                                "tool_call_id": tc.id,
                                "name": tc.name,
                                "status": result.status,
                                "output": result.output,
                                "error": result.error,
                                "execution_time_ms": result.execution_time_ms,
                            },
                        )
                    continue

                content = model_response.content or ""
                for chunk in chunk_text(content):
                    yield StreamEvent(event_type="token", data={"token": chunk})
                self._record_assistant(session, model_response, content)
                finished = True
                break

            if not finished:
                msg = f"O agente atingiu o limite de {self.max_steps} etapas sem concluir a resposta."
                self.session_manager.add_message(session.session_id, Message(role="assistant", content=msg))
                yield StreamEvent(event_type="token", data={"token": msg})
        except Exception as exc:  # erro inesperado: avisa o cliente em vez de cortar o stream
            yield StreamEvent(event_type="error", data={"message": f"Erro interno: {type(exc).__name__}"})

        yield StreamEvent(
            event_type="done",
            data={
                "session_id": session.session_id,
                "provider_used": last_response.provider if last_response else (provider or "local"),
                "model_used": last_response.model if last_response else "unknown",
                "execution_time_ms": int((time.time() - start_time) * 1000),
            },
        )
