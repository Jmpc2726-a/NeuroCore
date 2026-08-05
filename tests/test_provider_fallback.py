"""
Unit tests for multi-provider routing and deterministic offline fallback engine.
"""

import pytest
import os
from pathlib import Path

from neurocore.agent.providers import (
    OfflineSimulatedProvider,
    ProviderManager,
    Message,
    ProviderConfig,
)
from neurocore.agent.memory import SessionManager
from neurocore.agent.engine import AgentEngine
from neurocore.tools.executor import create_default_registry, ToolExecutor


@pytest.mark.asyncio
async def test_offline_simulated_provider_heuristics():
    provider = OfflineSimulatedProvider()
    assert provider.is_available() is True

    cfg = ProviderConfig(provider_name="local", model="simulated-local-v1")

    # Read file intent heuristic
    msgs_read = [Message(role="user", content="Read file 'README.md'")]
    resp_read = await provider.generate(msgs_read, [], cfg)
    assert resp_read.finish_reason == "tool_calls"
    assert len(resp_read.tool_calls) == 1
    assert resp_read.tool_calls[0].name == "read_file"
    assert resp_read.tool_calls[0].arguments["path"] == "README.md"

    # List directory intent heuristic
    msgs_list = [Message(role="user", content="List contents in directory 'src'")]
    resp_list = await provider.generate(msgs_list, [], cfg)
    assert resp_list.finish_reason == "tool_calls"
    assert resp_list.tool_calls[0].name == "list_dir"

    # Run command intent heuristic
    msgs_cmd = [Message(role="user", content="Run command 'pytest tests/'")]
    resp_cmd = await provider.generate(msgs_cmd, [], cfg)
    assert resp_cmd.finish_reason == "tool_calls"
    assert resp_cmd.tool_calls[0].name == "run_command"
    assert resp_cmd.tool_calls[0].arguments["command"] == "pytest tests/"

    # Tool output response synthesis
    msgs_tool = [
        Message(role="user", content="Read file 'test.txt'"),
        Message(role="assistant", tool_calls=resp_read.tool_calls),
        Message(role="tool", name="read_file", content="Hello World File Content"),
    ]
    resp_syn = await provider.generate(msgs_tool, [], cfg)
    assert resp_syn.finish_reason == "stop"
    assert "Hello World File Content" in resp_syn.content


@pytest.mark.asyncio
async def test_provider_manager_fallback_cascade(monkeypatch):
    # Ensure remote API keys are cleared or invalid
    monkeypatch.setenv("GEMINI_API_KEY", "")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    monkeypatch.setenv("OPENROUTER_API_KEY", "")

    pm = ProviderManager(config_provider="gemini")
    msgs = [Message(role="user", content="Hello world test")]

    # Should attempt gemini, fail/skip due to missing key, cascade down to local
    response = await pm.generate_with_fallback(msgs, [], requested_provider="gemini")
    assert response.provider == "local"
    assert response.fallback_occurred is True
    assert len(response.fallback_trace) > 0
    assert "API key missing or unconfigured" in response.fallback_trace[0]


@pytest.mark.asyncio
async def test_explicit_local_provider_selection():
    pm = ProviderManager()
    msgs = [Message(role="user", content="What is your status?")]

    response = await pm.generate_with_fallback(msgs, [], requested_provider="local")
    assert response.provider == "local"


@pytest.mark.asyncio
async def test_full_agent_engine_offline_execution(tmp_path: Path):
    registry = create_default_registry(workspace_root=tmp_path)
    executor = ToolExecutor(registry=registry)
    session_mgr = SessionManager(storage_dir=tmp_path / ".sessions")
    provider_mgr = ProviderManager(config_provider="local")

    engine = AgentEngine(
        provider_manager=provider_mgr,
        session_manager=session_mgr,
        tool_executor=executor,
    )

    # Execute file creation via agent engine
    prompt = "Create file 'hello.txt'"
    res = await engine.run(user_prompt=prompt, provider="local")
    assert res.session_id is not None
    assert len(res.tool_calls) > 0
    assert res.tool_calls[0]["name"] == "write_file"

    # Verify file was genuinely created on disk
    created_file = tmp_path / "hello.txt"
    assert created_file.exists()


@pytest.mark.asyncio
async def test_provider_fallback_cascade_multi_error_chain(monkeypatch):
    pm = ProviderManager(config_provider="gemini")
    msgs = [Message(role="user", content="Test prompt for multi-error cascade")]

    # Mock all remote providers to raise exceptions on generate()
    async def mock_gemini_generate(*args, **kwargs):
        raise RuntimeError("Gemini 500 Internal Server Error")

    async def mock_openai_generate(*args, **kwargs):
        raise RuntimeError("OpenAI 429 Rate Limit Exceeded")

    async def mock_anthropic_generate(*args, **kwargs):
        raise RuntimeError("Anthropic Connection Timeout")

    monkeypatch.setattr(pm.providers["gemini"], "is_available", lambda: True)
    monkeypatch.setattr(pm.providers["gemini"], "generate", mock_gemini_generate)

    monkeypatch.setattr(pm.providers["openai"], "is_available", lambda: True)
    monkeypatch.setattr(pm.providers["openai"], "generate", mock_openai_generate)

    monkeypatch.setattr(pm.providers["anthropic"], "is_available", lambda: True)
    monkeypatch.setattr(pm.providers["anthropic"], "generate", mock_anthropic_generate)

    response = await pm.generate_with_fallback(msgs, [], requested_provider="gemini")
    assert response.provider == "local"
    assert response.fallback_occurred is True
    assert len(response.fallback_trace) == 3
    assert "Gemini 500 Internal Server Error" in response.fallback_trace[0]
    assert "OpenAI 429 Rate Limit Exceeded" in response.fallback_trace[1]
    assert "Anthropic Connection Timeout" in response.fallback_trace[2]


@pytest.mark.asyncio
async def test_provider_fallback_preserves_session_context_over_multiturn(tmp_path: Path, monkeypatch):
    registry = create_default_registry(workspace_root=tmp_path)
    executor = ToolExecutor(registry=registry)
    session_mgr = SessionManager(storage_dir=tmp_path / ".sessions")
    provider_mgr = ProviderManager(config_provider="gemini")

    # Unconfigure remote keys so it falls back to local
    monkeypatch.setenv("GEMINI_API_KEY", "")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    monkeypatch.setenv("OPENROUTER_API_KEY", "")

    engine = AgentEngine(
        provider_manager=provider_mgr,
        session_manager=session_mgr,
        tool_executor=executor,
    )

    # Turn 1: User prompt triggers local tool call
    res1 = await engine.run(user_prompt="Create file 'turn1.txt'", provider="gemini")
    assert res1.provider_used == "local"
    assert res1.fallback_occurred is True
    session_id = res1.session_id

    # Turn 2: User prompt referencing previous turn
    res2 = await engine.run(user_prompt="Read file 'turn1.txt'", session_id=session_id, provider="gemini")
    assert res2.provider_used == "local"
    assert res2.session_id == session_id

    # Verify session history in memory
    sess = session_mgr.get_session(session_id)
    assert sess is not None
    assert len(sess.messages) >= 4  # User1, Assistant1 (tool), Tool1, User2, Assistant2...


@pytest.mark.asyncio
async def test_provider_fallback_stream_event_yields_fallback_notice(tmp_path: Path, monkeypatch):
    registry = create_default_registry(workspace_root=tmp_path)
    executor = ToolExecutor(registry=registry)
    session_mgr = SessionManager(storage_dir=tmp_path / ".sessions")
    provider_mgr = ProviderManager(config_provider="gemini")

    monkeypatch.setenv("GEMINI_API_KEY", "")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    monkeypatch.setenv("OPENROUTER_API_KEY", "")

    engine = AgentEngine(
        provider_manager=provider_mgr,
        session_manager=session_mgr,
        tool_executor=executor,
    )

    events = []
    async for event in engine.run_stream(user_prompt="Read file 'nonexistent.txt'", provider="gemini"):
        events.append(event)

    event_types = [e.event_type for e in events]
    assert "connected" in event_types
    assert "fallback" in event_types
    assert "done" in event_types

    fallback_ev = next(e for e in events if e.event_type == "fallback")
    assert fallback_ev.data["provider_used"] == "local"
    assert len(fallback_ev.data["fallback_trace"]) > 0


@pytest.mark.asyncio
async def test_provider_fallback_unknown_provider_name_defaults_safely():
    pm = ProviderManager()
    msgs = [Message(role="user", content="Ping")]

    # Request invalid/unknown provider name
    response = await pm.generate_with_fallback(msgs, [], requested_provider="unknown_provider_xyz")
    assert response.provider == "local"


@pytest.mark.asyncio
async def test_tier4_scenario4_multi_provider_auto_fallback_workflow(tmp_path: Path, monkeypatch):
    """
    Tier 4 Real-World Application Workflow (Scenario 4):
    Multi-Provider Auto Fallback & Outage Resilience.
    Simulates primary LLM outages across cloud providers (500, 429, Timeout),
    verifying automatic silent fallback to local provider, execution continuity,
    and output file creation without user-facing crash.
    """
    registry = create_default_registry(workspace_root=tmp_path)
    executor = ToolExecutor(registry=registry)
    session_mgr = SessionManager(storage_dir=tmp_path / ".sessions")
    provider_mgr = ProviderManager(config_provider="gemini")

    # Mock all cloud providers to simulate outages
    async def mock_gemini_fail(*args, **kwargs):
        raise RuntimeError("Gemini API HTTP 500 Outage")

    async def mock_openai_fail(*args, **kwargs):
        raise RuntimeError("OpenAI API HTTP 429 Rate Limit")

    async def mock_anthropic_fail(*args, **kwargs):
        raise RuntimeError("Anthropic Network Timeout")

    monkeypatch.setattr(provider_mgr.providers["gemini"], "is_available", lambda: True)
    monkeypatch.setattr(provider_mgr.providers["gemini"], "generate", mock_gemini_fail)

    monkeypatch.setattr(provider_mgr.providers["openai"], "is_available", lambda: True)
    monkeypatch.setattr(provider_mgr.providers["openai"], "generate", mock_openai_fail)

    monkeypatch.setattr(provider_mgr.providers["anthropic"], "is_available", lambda: True)
    monkeypatch.setattr(provider_mgr.providers["anthropic"], "generate", mock_anthropic_fail)

    engine = AgentEngine(
        provider_manager=provider_mgr,
        session_manager=session_mgr,
        tool_executor=executor,
    )

    prompt = "Write file 'resilience_workflow.log' with content 'System resilience verified'"
    res = await engine.run(user_prompt=prompt, provider="gemini")

    assert res.provider_used == "local"
    assert res.fallback_occurred is True
    assert len(res.fallback_trace) == 3
    assert "Gemini API HTTP 500 Outage" in res.fallback_trace[0]
    assert "OpenAI API HTTP 429 Rate Limit" in res.fallback_trace[1]
    assert "Anthropic Network Timeout" in res.fallback_trace[2]

    # Verify file was written by fallback execution
    output_file = tmp_path / "resilience_workflow.log"
    assert output_file.exists()
    assert "System resilience verified" in output_file.read_text()

