import os
import pytest
from neurocore.agent.providers import (
    ProviderManager,
    OfflineSimulatedProvider,
    GeminiProvider,
    OpenAIProvider,
    AnthropicProvider,
    OpenRouterProvider,
    Message,
    ToolDefinition,
)
from neurocore.agent.memory import SessionManager, Session, trim_messages, estimate_message_tokens

@pytest.fixture
def clean_env(monkeypatch):
    """Ensure environment variables for API keys are cleared for 100% offline tests."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

def test_offline_simulated_provider_is_available():
    provider = OfflineSimulatedProvider()
    assert provider.is_available() is True

@pytest.mark.asyncio
async def test_offline_simulated_provider_intent_matching():
    provider = OfflineSimulatedProvider()
    
    # Test read file pattern matching
    msg_read = Message(role="user", content="Read file main.py")
    resp_read = await provider.generate([msg_read], [], None)
    assert len(resp_read.tool_calls) == 1
    assert resp_read.tool_calls[0].name == "read_file"
    assert resp_read.tool_calls[0].arguments["path"] == "main.py"

    # Test run command pattern matching
    msg_cmd = Message(role="user", content="Run command pytest tests/")
    resp_cmd = await provider.generate([msg_cmd], [], None)
    assert len(resp_cmd.tool_calls) == 1
    assert resp_cmd.tool_calls[0].name == "run_command"
    assert resp_cmd.tool_calls[0].arguments["command"] == "pytest tests/"

@pytest.mark.asyncio
async def test_provider_adapters_availability(clean_env):
    gemini = GeminiProvider()
    openai = OpenAIProvider()
    anthropic = AnthropicProvider()
    openrouter = OpenRouterProvider()
    
    assert gemini.is_available() is False
    assert openai.is_available() is False
    assert anthropic.is_available() is False
    assert openrouter.is_available() is False

def test_provider_manager_get_available_providers(clean_env):
    pm = ProviderManager()
    avail = pm.get_available_providers()
    assert "local" in avail
    assert "gemini" in avail
    assert "openai" in avail
    assert "anthropic" in avail
    assert "openrouter" in avail
    assert avail["local"]["available"] is True
    assert avail["local"]["configured"] is True
    assert avail["gemini"]["available"] is False
    assert avail["openai"]["available"] is False
    assert avail["anthropic"]["available"] is False
    assert avail["openrouter"]["available"] is False

@pytest.mark.asyncio
async def test_provider_manager_fallback_cascade(clean_env):
    """
    Verifies that when external API keys are missing, ProviderManager cascades
    from requested primary provider down to local OfflineSimulatedProvider cleanly.
    """
    pm = ProviderManager(config_provider="gemini")
    
    msg = Message(role="user", content="List directory contents")
    resp = await pm.generate_with_fallback(
        messages=[msg],
        tools=[],
        requested_provider="gemini"
    )
    
    assert resp.provider == "local"
    assert resp.fallback_occurred is True
    assert len(resp.fallback_trace) > 0
    assert any("gemini" in item.lower() for item in resp.fallback_trace)

def test_session_manager_crud(tmp_path):
    storage_dir = str(tmp_path / "sessions")
    sm = SessionManager(storage_dir=storage_dir)

    session = sm.create_session(provider="gemini")
    sid = session.session_id

    # Add messages
    sm.add_message(sid, Message(role="user", content="Hello"))
    sm.add_message(sid, Message(role="assistant", content="Hi there!"))

    # Fetch session
    fetched = sm.get_session(sid)
    assert fetched is not None
    assert len(fetched.messages) == 2

    # Verify disk persistence
    assert os.path.exists(os.path.join(storage_dir, f"{sid}.json"))

    # Clear session
    sm.clear_session(sid)
    assert len(sm.get_session(sid).messages) == 0

    # Delete session
    sm.delete_session(sid)
    assert sm.get_session(sid) is None

def test_trim_messages_sliding_window():
    messages = [
        Message(role="user", content="Turn 1 user"),
        Message(role="assistant", content="Turn 1 assistant"),
        Message(role="user", content="Turn 2 user"),
        Message(role="assistant", content="Turn 2 assistant"),
        Message(role="user", content="Turn 3 user"),
        Message(role="assistant", content="Turn 3 assistant"),
    ]
    
    # Restrict max tokens so only last turns fit
    trimmed = trim_messages(messages, max_tokens=30, system_prompt="System")
    assert len(trimmed) < len(messages)
    assert trimmed[0].role == "user"  # Truncation boundary must align on user message
