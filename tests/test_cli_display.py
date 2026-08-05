import io
import json
import pytest
from rich.console import Console

from neurocore.cli.display import (
    DisplayEngine,
    DisplayManager,
    StreamRenderer,
    SlashCommandHandler,
    RichFormatter,
    PlainFormatter,
    JSONFormatter,
    NEUROCORE_THEME,
)

@pytest.fixture
def memory_console():
    output_stream = io.StringIO()
    console = Console(
        file=output_stream,
        force_terminal=True,
        width=100,
        color_system="truecolor",
        theme=NEUROCORE_THEME
    )
    return console, output_stream

@pytest.fixture
def display_engine(memory_console):
    console, stream = memory_console
    engine = DisplayEngine(console=console)
    return engine, console, stream

# ============================================================================
# 1. Display Engine Basics (Tier 1 & Tier 2)
# ============================================================================

def test_display_engine_init(memory_console):
    console, stream = memory_console
    engine = DisplayEngine(console=console)
    assert engine.get_console() == console
    assert engine.plain_mode is False
    assert engine.current_provider == "local"

def test_set_plain_mode(display_engine):
    engine, console, stream = display_engine
    engine.set_plain_mode(True)
    assert engine.plain_mode is True
    engine.render_info("Plain test message")
    output = stream.getvalue()
    assert "[INFO] Plain test message" in output

def test_render_welcome_banner(display_engine):
    engine, console, stream = display_engine
    engine.render_welcome_banner()
    output = stream.getvalue()
    assert "NEUROCORE" in output
    assert "Autonomous AI Coding" in output or "Interactive REPL" in output

def test_render_welcome_banner_plain_mode(display_engine):
    engine, console, stream = display_engine
    engine.set_plain_mode(True)
    engine.render_welcome_banner()
    output = stream.getvalue()
    assert "=== NeuroCore Code Agent CLI" in output

def test_render_status_bar(display_engine):
    engine, console, stream = display_engine
    engine.render_status_bar(
        provider="gemini",
        model="gemini-2.5-flash",
        workspace="/home/user/project",
        tokens=1234,
        status="READY"
    )
    output = stream.getvalue()
    assert "gemini" in output
    assert "gemini-2.5-flash" in output
    assert "1,234" in output

    state_dict = {
        "provider": "anthropic",
        "model": "claude-3-5-sonnet",
        "workspace": "/home/user/app",
        "tokens": 5678,
        "status": "ACTIVE"
    }
    engine.render_status_bar(state_dict)
    output_updated = stream.getvalue()
    assert "anthropic" in output_updated
    assert "claude-3-5-sonnet" in output_updated
    assert "5,678" in output_updated

def test_render_status_bar_plain_mode(display_engine):
    engine, console, stream = display_engine
    engine.set_plain_mode(True)
    engine.render_status_bar(provider="gemini", model="gemini-2.5-flash")
    # Plain mode ignores graphical status bar
    assert stream.getvalue() == ""

def test_render_notifications(display_engine):
    engine, console, stream = display_engine
    engine.render_info("System initialized")
    engine.render_warning("Rate limit warning")
    engine.render_error("Connection refused")
    engine.render_success("Task completed")

    output = stream.getvalue()
    assert "System initialized" in output
    assert "Rate limit warning" in output
    assert "Connection refused" in output
    assert "Task completed" in output

# ============================================================================
# 2. Tool Execution Tracing Panels (Tier 1 & Tier 2)
# ============================================================================

def test_render_tool_start(display_engine):
    engine, console, stream = display_engine
    args = {"filename": "test.py", "lines": [1, 10]}
    engine.render_tool_start(tool_name="read_file", call_id="call_abc12345", args=args)
    output = stream.getvalue()
    assert "read_file" in output
    assert "RUNNING" in output
    assert "call_abc" in output
    assert "test.py" in output

def test_render_tool_end_success_and_truncation(display_engine):
    engine, console, stream = display_engine
    long_output = "\n".join([f"Line {i}" for i in range(50)])
    engine.render_tool_end(
        tool_name="list_dir",
        call_id="call_123",
        status="success",
        duration_ms=45,
        output=long_output
    )

    output = stream.getvalue()
    assert "list_dir" in output
    assert "SUCCESS" in output
    assert "45ms" in output
    assert "Line 0" in output
    assert "Line 29" in output
    assert "lines truncated" in output

def test_render_tool_end_failure(display_engine):
    engine, console, stream = display_engine
    engine.render_tool_end(
        tool_name="run_command",
        call_id="call_456",
        status="failed",
        duration_ms=120,
        output=None,
        error="Permission denied"
    )

    output = stream.getvalue()
    assert "FAILED" in output
    assert "120ms" in output
    assert "Permission denied" in output

def test_render_tool_end_write_file_delegation(display_engine):
    engine, console, stream = display_engine
    out = {
        "path": "app.py",
        "diff": "--- a/app.py\n+++ b/app.py\n@@ -1 +1 @@\n-old\n+new\n"
    }
    engine.render_tool_end(
        tool_name="write_file",
        call_id="call_write",
        status="success",
        duration_ms=20,
        output=out
    )
    output = stream.getvalue()
    assert "Code Diff" in output
    assert "app.py" in output

def test_render_tool_trace_wrapper(display_engine):
    engine, console, stream = display_engine
    engine.render_tool_trace(
        tool_name="grep_search",
        call_id="call_trace_1",
        arguments={"pattern": "foo"},
        status="running"
    )
    assert "RUNNING" in stream.getvalue()

    engine.render_tool_trace(
        tool_name="grep_search",
        call_id="call_trace_1",
        arguments={"pattern": "foo"},
        status="success",
        duration_ms=10,
        output="Found 1 match"
    )
    assert "SUCCESS" in stream.getvalue()

# ============================================================================
# 3. Code Diff & Command Output Panels (Tier 1 & Tier 2)
# ============================================================================

def test_render_code_diff(display_engine):
    engine, console, stream = display_engine
    diff_text = (
        "--- a/config.py\n"
        "+++ b/config.py\n"
        "@@ -1,3 +1,3 @@\n"
        " DEBUG = False\n"
        "-PORT = 8000\n"
        "+PORT = 9000\n"
    )
    engine.render_code_diff(file_path="config.py", diff_text=diff_text)
    output = stream.getvalue()
    assert "config.py" in output
    assert "PORT = 8000" in output
    assert "PORT = 9000" in output
    assert "+1" in output and "-1" in output

def test_render_code_diff_from_raw_texts(display_engine):
    engine, console, stream = display_engine
    engine.render_code_diff(
        file_path="script.py",
        diff_text="",
        original_code="print('v1')\n",
        modified_code="print('v2')\n"
    )
    output = stream.getvalue()
    assert "script.py" in output
    assert "print('v1')" in output or "print('v2')" in output

def test_render_command_output(display_engine):
    engine, console, stream = display_engine
    engine.render_command_output(
        command="pytest tests/",
        cwd="/workspace/app",
        exit_code=0,
        duration_ms=250,
        stdout="2 passed in 0.1s",
        stderr=""
    )
    output = stream.getvalue()
    assert "pytest tests/" in output
    assert "/workspace/app" in output
    assert "SUCCESS" in output
    assert "2 passed in 0.1s" in output

# ============================================================================
# 4. Slash Commands & Formatters (Tier 1 & Tier 2)
# ============================================================================

@pytest.mark.asyncio
async def test_slash_command_handler(display_engine):
    engine, console, stream = display_engine
    handler = SlashCommandHandler(engine)

    res_help = await handler.handle_command("/help", "", "local", "heuristic", ".", 0, "sess_1")
    assert res_help["action"] == "help"
    assert "NeuroCore REPL Slash Commands" in stream.getvalue()

    res_prov_query = await handler.handle_command("/provider", "", "local", "heuristic", ".", 0, "sess_1")
    assert res_prov_query["action"] == "provider_info"

    res_prov_change = await handler.handle_command("/provider", "openai", "local", "heuristic", ".", 0, "sess_1")
    assert res_prov_change["action"] == "provider_changed"
    assert res_prov_change["new_provider"] == "openai"

    res_status = await handler.handle_command("/status", "", "openai", "gpt-4o", ".", 500, "sess_1")
    assert res_status["action"] == "status"
    assert "Session & System Status" in stream.getvalue()

    res_reset = await handler.handle_command("/reset", "", "openai", "gpt-4o", ".", 500, "sess_1")
    assert res_reset["action"] == "reset"

    res_exit = await handler.handle_command("/exit", "", "openai", "gpt-4o", ".", 500, "sess_1")
    assert res_exit["action"] == "exit"

@pytest.mark.asyncio
async def test_slash_command_handler_invalid_provider(display_engine):
    engine, console, stream = display_engine
    handler = SlashCommandHandler(engine)
    res = await handler.handle_command("/provider", "invalid_llm", "local", "heuristic", ".", 0, "sess_1")
    assert res["action"] == "error"
    assert "Invalid provider" in stream.getvalue()

@pytest.mark.asyncio
async def test_slash_command_handler_unknown_command(display_engine):
    engine, console, stream = display_engine
    handler = SlashCommandHandler(engine)
    res = await handler.handle_command("/foobar", "", "local", "heuristic", ".", 0, "sess_1")
    assert res["action"] == "unknown"

def test_formatters(display_engine, capsys):
    engine, console, stream = display_engine

    rich_fmt = RichFormatter(engine)
    rich_fmt.format_final("Rich markdown **bold** response")
    assert "Rich markdown" in stream.getvalue()

    plain_fmt = PlainFormatter()
    plain_fmt.format_final("Plain text response")
    captured = capsys.readouterr()
    assert "Plain text response" in captured.out

    json_fmt = JSONFormatter()
    json_fmt.format_token("Hello ")
    json_fmt.format_token("World")
    json_fmt.add_tool_call({"name": "read_file", "status": "success"})
    json_fmt.format_final(success=True)

    json_captured = capsys.readouterr()
    data = json.loads(json_captured.out)
    assert data["success"] is True
    assert data["response"] == "Hello World"
    assert len(data["tool_calls"]) == 1
    assert data["tool_calls"][0]["name"] == "read_file"
