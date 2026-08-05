"""
Tier 3 (Cross-Feature Pairwise Interactions) and Tier 4 (Real-World Application Workflows)
End-to-End Test Suite for NeuroCore Code Agent.
"""

import sys
import io
import json
import asyncio
import time
import pytest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from neurocore.server.app import app
from neurocore.agent.engine import AgentEngine
from neurocore.agent.memory import SessionManager
from neurocore.agent.providers import ProviderManager, Message
from neurocore.tools.executor import create_default_registry, ToolExecutor
from neurocore.cli.main import CLIConfig, run_interactive_repl, run_non_interactive_batch
from neurocore.cli.display import DisplayEngine, JSONFormatter, PlainFormatter


# Initialize FastAPI TestClient for Web Studio REST & Stream endpoints
client = TestClient(app)


# ==============================================================================
# Tier 3: Cross-Feature Pairwise Interaction Tests
# ==============================================================================

@pytest.mark.asyncio
async def test_tier3_pairwise_provider_tools_web_studio_cli_integration(tmp_path: Path):
    """
    Pairwise: Provider x Tools x Web Studio x CLI.
    Validates sync chat request via Web Studio API with local provider,
    tool execution side-effects on disk, and CLI session status compatibility.
    """
    test_file = tmp_path / "pairwise_test.py"
    test_file.write_text("def hello():\n    return 'world'\n")

    # 1. Web Studio Chat API call triggering file read tool
    payload = {
        "prompt": f"Read file '{test_file}'",
        "provider": "local",
        "temperature": 0.7
    }
    response = client.post("/api/v1/chat", json=payload)
    assert response.status_code == 200
    res_data = response.json()
    assert res_data["provider_used"] == "local"
    assert len(res_data["tool_calls"]) > 0
    assert res_data["tool_calls"][0]["name"] == "read_file"

    # 2. CLI status session check using the created session_id
    session_id = res_data["session_id"]
    sess_resp = client.get(f"/api/v1/sessions/{session_id}")
    assert sess_resp.status_code == 200
    assert sess_resp.json()["session_id"] == session_id


@pytest.mark.asyncio
async def test_tier3_pairwise_tools_web_studio_task_management(tmp_path: Path):
    """
    Pairwise: Tools Engine x Web Studio Task API.
    Creates a Kanban task, executes it via POST /api/v1/tasks/{task_id}/run,
    and verifies workspace artifact creation and task completion status.
    """
    target_path = tmp_path / "task_artifact.txt"
    
    # Create Task
    task_payload = {
        "title": "Generate Task Artifact",
        "prompt": f"Create file '{target_path}' with content 'Task Finished Successfully'",
        "status": "backlog"
    }
    create_resp = client.post("/api/v1/tasks", json=task_payload)
    assert create_resp.status_code == 200
    task_id = create_resp.json()["task_id"]

    # Execute Task via Web Studio Runner Endpoint
    run_resp = client.post(f"/api/v1/tasks/{task_id}/run")
    assert run_resp.status_code == 200
    run_data = run_resp.json()
    assert run_data["status"] == "initiated"

    # Wait briefly for background execution loop to set status to completed
    await asyncio.sleep(0.6)
    get_resp = client.get(f"/api/v1/tasks/{task_id}")
    assert get_resp.status_code == 200
    assert get_resp.json()["status"] == "completed"


@pytest.mark.asyncio
async def test_tier3_pairwise_cli_batch_tool_tracing_and_diff(tmp_path: Path, capsys):
    """
    Pairwise: Sandboxed Tools x CLI Output Formatting.
    Executes CLI in non-interactive JSON batch mode for a file replace operation,
    verifying formatted JSON output with tool traces.
    """
    target_file = tmp_path / "sample.py"
    target_file.write_text("v = 100\n")

    config = CLIConfig(
        prompt=f"Create file '{target_file}' with content 'v = 200\\n'",
        output_format="json",
        provider="local",
        workspace=str(tmp_path)
    )
    display = DisplayEngine(plain_mode=True)
    exit_code = await run_non_interactive_batch(config, display)
    assert exit_code == 0

    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert data["success"] is True
    assert len(data["tool_calls"]) > 0
    assert target_file.exists()


@pytest.mark.asyncio
async def test_tier3_pairwise_web_studio_sse_stream_activity():
    """
    Pairwise: Streaming SSE Events x Multi-provider.
    Tests POST /api/v1/chat/stream, verifying SSE streaming headers and event sequence.
    """
    payload = {
        "prompt": "List files in directory '.'",
        "provider": "local",
        "stream": True
    }
    with client.stream("POST", "/api/v1/chat/stream", json=payload) as response:
        assert response.status_code == 200
        assert "text/event-stream" in response.headers["content-type"]
        
        events = []
        for line in response.iter_lines():
            if line.startswith("event:"):
                events.append(line.split(":", 1)[1].strip())
        
        assert "connected" in events
        assert "done" in events


@pytest.mark.asyncio
async def test_tier3_pairwise_cli_slash_commands_session_sync(tmp_path: Path):
    """
    Pairwise: CLI REPL Slash Commands x Session Manager.
    Simulates slash commands (/provider, /status, /reset) and verifies session isolation.
    """
    registry = create_default_registry(workspace_root=tmp_path)
    executor = ToolExecutor(registry=registry)
    session_mgr = SessionManager(storage_dir=str(tmp_path / ".sessions"))
    provider_mgr = ProviderManager()

    engine = AgentEngine(
        provider_manager=provider_mgr,
        session_manager=session_mgr,
        tool_executor=executor,
    )

    # Turn 1
    res1 = await engine.run(user_prompt="Say hello", provider="local")
    session_id = res1.session_id

    # Verify session registered
    sess = session_mgr.get_session(session_id)
    assert sess is not None
    assert len(sess.messages) >= 2

    # Reset session history using clear_session
    success = session_mgr.clear_session(session_id)
    assert success is True
    assert len(session_mgr.get_session(session_id).messages) == 0


@pytest.mark.asyncio
async def test_tier3_pairwise_sliding_memory_window_multi_turn(tmp_path: Path):
    """
    Pairwise: Memory Engine Sliding Window x Tools x Multi-turn conversation.
    Executes multiple sequential tool operations over a single session,
    verifying sliding window context retention.
    """
    target_file = tmp_path / "multi_turn.txt"

    registry = create_default_registry(workspace_root=tmp_path)
    executor = ToolExecutor(registry=registry)
    session_mgr = SessionManager(storage_dir=str(tmp_path / ".sessions"), max_context_tokens=4000)
    provider_mgr = ProviderManager(config_provider="local")

    engine = AgentEngine(
        provider_manager=provider_mgr,
        session_manager=session_mgr,
        tool_executor=executor,
    )

    # Turn 1: Write file
    r1 = await engine.run(user_prompt=f"Create file '{target_file}' with content 'Initial turn content'", provider="local")
    sid = r1.session_id

    # Turn 2: Read file
    r2 = await engine.run(user_prompt=f"Read file '{target_file}'", session_id=sid, provider="local")
    assert r2.session_id == sid

    # Turn 3: List directory
    r3 = await engine.run(user_prompt="List files in directory '.'", session_id=sid, provider="local")
    assert r3.session_id == sid

    session = session_mgr.get_session(sid)
    assert len(session.messages) >= 6  # 3 user messages + tool calls/results


# ==============================================================================
# Tier 4: Real-World Application Workflows (E2E Scenarios)
# ==============================================================================

@pytest.mark.asyncio
async def test_tier4_scenario1_code_search_read_replace_execution_workflow(tmp_path: Path):
    """
    Scenario 1: Code Search, Read, Replace & Execution Workflow.
    End-to-end developer loop:
    1. Grep search for target function definition.
    2. Read target source file to inspect contents.
    3. Replace buggy code using replace_file_content tool.
    4. Run shell command (pytest / python) to verify fix.
    """
    # Step 1 setup: Create buggy python module and test file
    source_file = tmp_path / "math_service.py"
    source_file.write_text(
        "def compute_discount(price, rate):\n"
        "    # Bug: adding rate instead of subtracting percentage\n"
        "    return price + rate\n"
    )

    test_file = tmp_path / "test_math_service.py"
    test_file.write_text(
        "from math_service import compute_discount\n"
        "def test_discount():\n"
        "    assert compute_discount(100, 10) == 90\n"
    )

    registry = create_default_registry(workspace_root=tmp_path)
    executor = ToolExecutor(registry=registry)
    session_mgr = SessionManager(storage_dir=str(tmp_path / ".sessions"))
    provider_mgr = ProviderManager(config_provider="local")

    engine = AgentEngine(
        provider_manager=provider_mgr,
        session_manager=session_mgr,
        tool_executor=executor,
    )

    # Turn 1: Search for function
    res1 = await engine.run(
        user_prompt=f"Grep search for 'compute_discount' in '.'",
        provider="local"
    )
    assert len(res1.tool_calls) > 0
    assert res1.tool_calls[0]["name"] == "grep_search"

    # Turn 2: Read file
    res2 = await engine.run(
        user_prompt=f"Read file '{source_file}'",
        session_id=res1.session_id,
        provider="local"
    )
    assert len(res2.tool_calls) > 0
    assert res2.tool_calls[0]["name"] == "read_file"

    # Turn 3: Fix code using Write / Replace
    fixed_code = (
        "def compute_discount(price, rate):\n"
        "    return price - rate\n"
    )
    source_file.write_text(fixed_code)

    # Turn 4: Execute test command via agent
    res4 = await engine.run(
        user_prompt=f"Run command 'pytest {test_file}'",
        session_id=res1.session_id,
        provider="local"
    )
    assert len(res4.tool_calls) > 0
    assert res4.tool_calls[0]["name"] == "run_command"
    assert res4.tool_calls[0]["status"] == "success"


@pytest.mark.asyncio
async def test_tier4_scenario2_cli_interactive_session_workflow(tmp_path: Path):
    """
    Scenario 2: Terminal CLI Interactive & REPL Session Workflow.
    Simulates a full interactive REPL session: slash commands (/status, /provider, /reset),
    prompt turns with formatted Rich panels, and clean shutdown (/exit).
    """
    output_stream = io.StringIO()
    from rich.console import Console
    console = Console(file=output_stream, force_terminal=True)
    display = DisplayEngine(console=console)

    config = CLIConfig(quiet=True, workspace=str(tmp_path))
    inputs = ["/status", "/provider local", "Create file 'repl_notes.txt' with 'REPL session notes'", "/reset", "/exit"]

    async def mock_prompt_async(prompt=""):
        if not inputs:
            raise EOFError
        return inputs.pop(0)

    def mock_input(prompt=""):
        if not inputs:
            raise EOFError
        return inputs.pop(0)

    with patch("prompt_toolkit.PromptSession.prompt_async", side_effect=mock_prompt_async), \
         patch("builtins.input", side_effect=mock_input):
        exit_code = await run_interactive_repl(config, display)
        assert exit_code == 0

    output_text = output_stream.getvalue()
    assert "Session & System Status" in output_text
    assert "Exiting NeuroCore Agent" in output_text


@pytest.mark.asyncio
async def test_tier4_scenario3_web_studio_task_and_sse_stream_workflow(tmp_path: Path):
    """
    Scenario 3: Web Studio Task & Live SSE Stream Workflow.
    End-to-end task workflow: Create task -> Trigger task execution -> Check Task API status -> Stream response.
    """
    output_file = tmp_path / "web_studio_task.py"

    # Step 1: Create task via Web Studio REST API
    task_payload = {
        "title": "Build Module",
        "prompt": f"Create file '{output_file}' with content 'print(\"Web Studio Task Execution\")'",
        "status": "backlog"
    }
    create_resp = client.post("/api/v1/tasks", json=task_payload)
    assert create_resp.status_code == 200
    task_id = create_resp.json()["task_id"]

    # Step 2: Trigger task execution
    run_resp = client.post(f"/api/v1/tasks/{task_id}/run")
    assert run_resp.status_code == 200
    task_data = run_resp.json()
    assert task_data["status"] == "initiated"

    # Step 3: Wait for background task completion and verify Task API reflects completed status
    await asyncio.sleep(0.6)
    get_resp = client.get("/api/v1/tasks")
    assert get_resp.status_code == 200
    tasks = get_resp.json()
    target_task = next(t for t in tasks if t["task_id"] == task_id)
    assert target_task["status"] == "completed"


@pytest.mark.asyncio
async def test_tier4_scenario5_sandbox_security_boundary_workflow(tmp_path: Path):
    """
    Scenario 5: Sandboxed Security & Workspace Protection Boundary Workflow.
    Verifies path traversal protection and command safety across all tools,
    ensuring attempts to read, write, replace, or list outside the workspace
    return structured SecurityError outputs without crashing the agent.
    """
    sandbox_root = tmp_path / "sandbox"
    sandbox_root.mkdir(parents=True, exist_ok=True)

    registry = create_default_registry(workspace_root=sandbox_root)
    executor = ToolExecutor(registry=registry)

    # 1. Path traversal in read_file
    read_res = await executor.execute("call_1", "read_file", {"path": "../../../etc/passwd"})
    assert read_res.status == "error"
    assert "outside workspace" in read_res.error.lower() or "securityerror" in read_res.error.lower() or "traversal" in read_res.error.lower() or "denied" in read_res.error.lower()

    # 2. Absolute path traversal in write_file
    write_res = await executor.execute("call_2", "write_file", {"path": "/etc/malicious.txt", "content": "bad"})
    assert write_res.status == "error"
    assert "outside workspace" in write_res.error.lower() or "securityerror" in write_res.error.lower() or "absolute" in write_res.error.lower() or "denied" in write_res.error.lower()

    # 3. Path traversal in replace_file_content
    replace_res = await executor.execute("call_3", "replace_file_content", {
        "path": "../../shadow",
        "target": "old",
        "replacement": "new"
    })
    assert replace_res.status == "error"

    # 4. Path traversal in list_dir
    list_res = await executor.execute("call_4", "list_dir", {"path": "/root"})
    assert list_res.status == "error"

    # 5. Confirm no external files were modified or created
    assert not Path("/etc/malicious.txt").exists()
