"""Testes das correções de segurança e da conversão de mensagens dos provedores."""

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from neurocore.agent.memory import SessionManager
from neurocore.agent.providers import (
    AnthropicProvider,
    GeminiProvider,
    Message,
    ModelResponse,
    ProviderManager,
    ToolCall,
    _to_openai_messages,
)
from neurocore.config import config
from neurocore.server.app import app
from neurocore.server.security import SlidingWindowRateLimiter
from neurocore.tools.executor import create_default_registry
from neurocore.tools.file_tools import GrepSearchTool, ListDirTool, ReadFileTool, WriteFileTool
from neurocore.tools.shell_tools import sanitized_env

client = TestClient(app)


# ---------------------------------------------------------------------------
# Chave de API do usuário não pode vazar para outros visitantes
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_user_api_key_is_not_stored_on_shared_provider(monkeypatch):
    pm = ProviderManager(config_provider="local")
    seen_keys = []

    async def fake_generate(self, messages, tools, cfg=None):
        seen_keys.append(self.api_key)
        return ModelResponse(content="ok", provider="openai", model=cfg.model)

    monkeypatch.setattr("neurocore.agent.providers.OpenAIProvider.generate", fake_generate)
    shared_before = pm.providers["openai"].api_key

    resp = await pm.generate_with_fallback(
        [Message(role="user", content="oi")], [], requested_provider="openai", api_key="sk-user-secret"
    )
    assert resp.content == "ok"
    assert seen_keys == ["sk-user-secret"]
    assert pm.providers["openai"].api_key == shared_before


@pytest.mark.asyncio
async def test_model_override_only_applies_to_primary_provider(monkeypatch):
    pm = ProviderManager(config_provider="local")
    used = {}

    async def fail(*a, **k):
        raise RuntimeError("boom")

    async def anthropic_ok(self, messages, tools, cfg=None):
        used["model"] = cfg.model
        return ModelResponse(content="ok", provider="anthropic", model=cfg.model)

    monkeypatch.setattr(pm.providers["gemini"], "is_available", lambda: True)
    monkeypatch.setattr(pm.providers["gemini"], "generate", fail)
    monkeypatch.setattr(pm.providers["openai"], "is_available", lambda: False)
    monkeypatch.setattr(pm.providers["anthropic"], "is_available", lambda: True)
    monkeypatch.setattr("neurocore.agent.providers.AnthropicProvider.generate", anthropic_ok)

    resp = await pm.generate_with_fallback(
        [Message(role="user", content="oi")], [], requested_provider="gemini", model_override="gemini-2.5-pro"
    )
    assert resp.provider == "anthropic"
    assert used["model"] == pm.providers["anthropic"].default_model
    assert resp.fallback_occurred is True


@pytest.mark.asyncio
async def test_fallback_trace_redacts_api_key(monkeypatch):
    pm = ProviderManager(config_provider="local")

    async def leaky(self, messages, tools, cfg=None):
        raise RuntimeError(f"bad key {self.api_key}")

    monkeypatch.setattr("neurocore.agent.providers.GeminiProvider.generate", leaky)
    resp = await pm.generate_with_fallback(
        [Message(role="user", content="oi")], [], requested_provider="gemini", api_key="AIzaSECRET123456"
    )
    assert resp.provider == "local"
    assert all("AIzaSECRET123456" not in t for t in resp.fallback_trace)


# ---------------------------------------------------------------------------
# Conversão de mensagens
# ---------------------------------------------------------------------------

def _history():
    tc = ToolCall(id="call_1", name="read_file", arguments={"path": "a.py"})
    return [
        Message(role="system", content="sys"),
        Message(role="user", content="leia a.py"),
        Message(role="assistant", content=None, tool_calls=[tc]),
        Message(role="tool", name="read_file", tool_call_id="call_1", content="print(1)"),
        Message(role="assistant", content="O arquivo imprime 1."),
        Message(role="user", content="obrigado"),
    ]


def test_anthropic_message_format_uses_tool_blocks():
    system, msgs = AnthropicProvider._build_messages(_history())
    assert system == "sys"
    assert [m["role"] for m in msgs] == ["user", "assistant", "user", "assistant", "user"]
    assert msgs[1]["content"][0] == {"type": "tool_use", "id": "call_1", "name": "read_file", "input": {"path": "a.py"}}
    assert msgs[2]["content"][0]["type"] == "tool_result"
    assert msgs[2]["content"][0]["tool_use_id"] == "call_1"


def test_anthropic_replays_raw_blocks_unchanged():
    raw = [{"type": "thinking", "thinking": "", "signature": "sig"}, {"type": "text", "text": "oi"}]
    _, msgs = AnthropicProvider._build_messages([
        Message(role="user", content="a"),
        Message(role="assistant", content="oi", raw_provider="anthropic", raw_content=raw),
        Message(role="user", content="b"),
    ])
    assert msgs[1]["content"] == raw


def test_gemini_format_uses_system_instruction_and_function_parts():
    system, contents = GeminiProvider._build_contents(_history())
    assert system == "sys"
    assert contents[0]["role"] == "user"
    assert "functionCall" in contents[1]["parts"][0]
    assert "functionResponse" in contents[2]["parts"][0]


def test_openai_format_tool_messages():
    msgs = _to_openai_messages(_history())
    tool_msg = next(m for m in msgs if m["role"] == "tool")
    assert tool_msg == {"role": "tool", "tool_call_id": "call_1", "content": "print(1)"}
    assistant = next(m for m in msgs if m.get("tool_calls"))
    assert assistant["tool_calls"][0]["function"]["name"] == "read_file"


# ---------------------------------------------------------------------------
# Sessões e arquivos protegidos
# ---------------------------------------------------------------------------

def test_session_id_path_traversal_is_rejected(tmp_path: Path):
    sm = SessionManager(storage_dir=str(tmp_path / ".sessions"))
    with pytest.raises(ValueError):
        sm.create_session(session_id="../../evil")
    assert not (tmp_path / "evil.json").exists()


def test_chat_rejects_malicious_session_id():
    resp = client.post("/api/v1/chat", json={"prompt": "oi", "provider": "local", "session_id": "../../x"})
    assert resp.status_code == 422


def test_protected_files_cannot_be_read_or_written(tmp_path: Path):
    (tmp_path / ".env").write_text("OPENAI_API_KEY=sk-secret")
    assert ReadFileTool(workspace_root=tmp_path).run(path=".env").status == "error"
    assert WriteFileTool(workspace_root=tmp_path).run(path=".env", content="x").status == "error"
    grep = GrepSearchTool(workspace_root=tmp_path).run(pattern="sk-secret", path=".")
    assert grep.status == "success" and grep.output == []
    listing = ListDirTool(workspace_root=tmp_path).run(path=".")
    assert all(item["name"] != ".env" for item in listing.output)


def test_shell_env_has_no_secrets(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-secret")
    monkeypatch.setenv("NEUROCORE_ACCESS_TOKEN", "tok")
    env = sanitized_env()
    assert "OPENAI_API_KEY" not in env
    assert "NEUROCORE_ACCESS_TOKEN" not in env
    assert "PATH" in env


def test_dangerous_tools_can_be_disabled(tmp_path: Path):
    names = {t.name for t in create_default_registry(tmp_path, allow_dangerous=False).list_tools()}
    assert "run_command" not in names and "write_file" not in names
    assert {"read_file", "list_dir", "grep_search"} <= names


# ---------------------------------------------------------------------------
# HTTP: cabeçalhos, autenticação, limites
# ---------------------------------------------------------------------------

def test_security_headers_present():
    resp = client.get("/")
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert resp.headers["x-frame-options"] == "DENY"
    assert "script-src 'self'" in resp.headers["content-security-policy"]


def test_access_token_required_when_configured(monkeypatch):
    monkeypatch.setattr(config, "ACCESS_TOKEN", "s3cret")
    assert client.get("/api/v1/health").status_code == 200
    assert client.get("/api/v1/tasks").status_code == 401
    assert client.get("/api/v1/tasks", headers={"X-NeuroCore-Token": "wrong"}).status_code == 401
    assert client.get("/api/v1/tasks", headers={"X-NeuroCore-Token": "s3cret"}).status_code == 200
    assert client.get("/api/v1/tasks", headers={"Authorization": "Bearer s3cret"}).status_code == 200


def test_rate_limiter_blocks_after_limit():
    limiter = SlidingWindowRateLimiter(limit=2, window_seconds=60)
    assert limiter.allow("ip")[0]
    assert limiter.allow("ip")[0]
    allowed, retry = limiter.allow("ip")
    assert not allowed and retry >= 1
    assert limiter.allow("other")[0]


def test_prompt_too_long_is_rejected():
    resp = client.post("/api/v1/chat", json={"prompt": "x" * (config.MAX_PROMPT_CHARS + 1), "provider": "local"})
    assert resp.status_code == 422


def test_task_status_must_be_valid():
    resp = client.post("/api/v1/tasks", json={"title": "t", "prompt": "p", "status": "hacked"})
    assert resp.status_code == 422


def test_document_download_rejects_traversal():
    assert client.get("/api/v1/documents/..%2F..%2Fetc%2Fpasswd").status_code == 404
    assert client.get("/api/v1/documents/README.md").status_code == 404


def test_internal_errors_do_not_leak_details(monkeypatch):
    from neurocore.server import routes

    async def explode(*a, **k):
        raise RuntimeError("secret internal detail")

    monkeypatch.setattr(routes.agent_engine, "run", explode)
    local = TestClient(app, raise_server_exceptions=False)
    resp = local.post("/api/v1/chat", json={"prompt": "oi", "provider": "local"})
    assert resp.status_code == 500
    assert "secret internal detail" not in resp.text


def test_portuguese_offline_intents():
    import asyncio
    from neurocore.agent.providers import OfflineSimulatedProvider

    p = OfflineSimulatedProvider()
    cases = {
        "Leia o arquivo pyproject.toml e explique": "read_file",
        "Liste os arquivos da pasta neurocore": "list_dir",
        "Procure onde a função run_stream é definida": "grep_search",
        "Execute os testes automatizados com pytest": "run_command",
    }
    for prompt, tool in cases.items():
        resp = asyncio.run(p.generate([Message(role="user", content=prompt)], []))
        assert resp.tool_calls and resp.tool_calls[0].name == tool, prompt
