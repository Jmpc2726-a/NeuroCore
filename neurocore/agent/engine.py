from __future__ import annotations
import json
import time
import uuid
import asyncio
from typing import List, Dict, Any, Optional, AsyncIterator
from pydantic import BaseModel, Field

from neurocore.agent.providers import (
    ProviderManager,
    Message,
    ToolCall,
    ModelResponse,
    StreamEvent,
    ToolDefinition,
)
from neurocore.agent.memory import SessionManager, Session
from neurocore.tools.executor import ToolExecutor

class ChatResponse(BaseModel):
    session_id: str
    message_id: str = Field(default_factory=lambda: f"msg_{uuid.uuid4().hex[:12]}")
    content: str
    provider_used: str
    model_used: str
    tool_calls: List[Dict[str, Any]] = []
    finish_reason: str = "stop"
    usage: Dict[str, int] = Field(default_factory=lambda: {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0})
    execution_time_ms: int = 0
    fallback_occurred: bool = False
    fallback_trace: List[str] = []


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
        session = self.session_manager.get_or_create_session(session_id, provider=provider or "gemini")
        
        user_msg = Message(role="user", content=user_prompt)
        self.session_manager.add_message(session.session_id, user_msg)

        all_tool_traces = []
        step = 0
        final_content = ""
        last_response: Optional[ModelResponse] = None

        tool_defs = self.tool_executor.get_tool_definitions() if tools_enabled else []

        while step < self.max_steps:
            step += 1
            messages_for_llm = [Message(role="system", content=session.system_prompt)] + session.messages

            model_response = await self.provider_manager.generate_with_fallback(
                messages=messages_for_llm,
                tools=tool_defs,
                requested_provider=provider or session.provider,
                model_override=model or session.model,
                temperature=temperature,
                api_key=api_key,
            )
            last_response = model_response

            if model_response.tool_calls:
                assistant_msg = Message(
                    role="assistant",
                    content=model_response.content,
                    tool_calls=model_response.tool_calls,
                )
                self.session_manager.add_message(session.session_id, assistant_msg)

                for tc in model_response.tool_calls:
                    result = await self.tool_executor.execute(
                        tool_call_id=tc.id,
                        name=tc.name,
                        arguments=tc.arguments,
                    )
                    all_tool_traces.append(result.model_dump())

                    tool_msg = Message(
                        role="tool",
                        content=str(result.output) if result.status == "success" else f"Error: {result.error}",
                        name=tc.name,
                        tool_call_id=tc.id,
                    )
                    self.session_manager.add_message(session.session_id, tool_msg)
                continue

            final_content = model_response.content or ""
            assistant_msg = Message(role="assistant", content=final_content)
            self.session_manager.add_message(session.session_id, assistant_msg)
            break

        if step >= self.max_steps and not final_content:
            final_content = f"Agent reached max steps ({self.max_steps}) limit."
            assistant_msg = Message(role="assistant", content=final_content)
            self.session_manager.add_message(session.session_id, assistant_msg)

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
            fallback_occurred=last_response.fallback_occurred if last_response else False,
            fallback_trace=last_response.fallback_trace if last_response else [],
        )

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
        session = self.session_manager.get_or_create_session(session_id, provider=provider or "gemini")

        yield StreamEvent(event_type="connected", data={"session_id": session.session_id})

        user_msg = Message(role="user", content=user_prompt)
        self.session_manager.add_message(session.session_id, user_msg)

        tool_defs = self.tool_executor.get_tool_definitions() if tools_enabled else []
        step = 0

        while step < self.max_steps:
            step += 1
            messages_for_llm = [Message(role="system", content=session.system_prompt)] + session.messages

            model_response = await self.provider_manager.generate_with_fallback(
                messages=messages_for_llm,
                tools=tool_defs,
                requested_provider=provider or session.provider,
                model_override=model or session.model,
                temperature=temperature,
                api_key=api_key,
            )

            if model_response.fallback_occurred:
                yield StreamEvent(
                    event_type="fallback",
                    data={
                        "provider_used": model_response.provider,
                        "fallback_trace": model_response.fallback_trace,
                    },
                )

            if model_response.tool_calls:
                assistant_msg = Message(
                    role="assistant",
                    content=model_response.content,
                    tool_calls=model_response.tool_calls,
                )
                self.session_manager.add_message(session.session_id, assistant_msg)

                for tc in model_response.tool_calls:
                    yield StreamEvent(
                        event_type="tool_start",
                        data={"tool_call_id": tc.id, "name": tc.name, "args": tc.arguments},
                    )

                    result = await self.tool_executor.execute(
                        tool_call_id=tc.id,
                        name=tc.name,
                        arguments=tc.arguments,
                    )

                    if isinstance(result.output, dict) and "diff" in result.output:
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
                            "execution_time_ms": result.execution_time_ms,
                        },
                    )

                    tool_msg = Message(
                        role="tool",
                        content=str(result.output) if result.status == "success" else f"Error: {result.error}",
                        name=tc.name,
                        tool_call_id=tc.id,
                    )
                    self.session_manager.add_message(session.session_id, tool_msg)
                continue

            content = model_response.content or ""
            for char in content:
                yield StreamEvent(event_type="token", data={"token": char})

            assistant_msg = Message(role="assistant", content=content)
            self.session_manager.add_message(session.session_id, assistant_msg)
            break

        elapsed_ms = int((time.time() - start_t if 'start_t' in locals() else start_time) * 1000)
        yield StreamEvent(
            event_type="done",
            data={
                "session_id": session.session_id,
                "execution_time_ms": elapsed_ms,
            },
        )
