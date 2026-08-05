import sys
import io
import json
import asyncio
import pytest
from unittest.mock import patch, MagicMock

from neurocore.cli.main import (
    CLIConfig,
    create_argument_parser,
    build_parser,
    parse_args,
    execute_agent_turn,
    run_non_interactive_batch,
    run_interactive_repl,
    CLIRunner,
    main,
)
from neurocore.cli.display import DisplayEngine, JSONFormatter, PlainFormatter, RichFormatter

# ============================================================================
# 1. Argument Parser Tests (Tier 1 & Tier 2)
# ============================================================================

def test_argument_parser_defaults():
    parser = create_argument_parser()
    args = parser.parse_args([])
    assert args.prompt is None
    assert args.output is None
    assert args.provider is None
    assert args.model is None
    assert args.workspace == "."
    assert args.session_id is None
    assert args.server_url == "http://127.0.0.1:8000"
    assert args.quiet is False

def test_argument_parser_overrides():
    parser = build_parser()
    args = parser.parse_args([
        "-p", "Analyze code",
        "-o", "json",
        "--provider", "gemini",
        "--model", "gemini-2.5-flash",
        "-w", "/tmp/test",
        "-s", "sess_custom",
        "-u", "http://localhost:9000",
        "-q"
    ])
    assert args.prompt == "Analyze code"
    assert args.output == "json"
    assert args.provider == "gemini"
    assert args.model == "gemini-2.5-flash"
    assert args.workspace == "/tmp/test"
    assert args.session_id == "sess_custom"
    assert args.server_url == "http://localhost:9000"
    assert args.quiet is True

def test_parse_args_helper():
    args = parse_args(["-p", "Test prompt"])
    assert args.prompt == "Test prompt"

# ============================================================================
# 2. Agent Turn Execution Tests (Tier 1 & Tier 2)
# ============================================================================

@pytest.mark.asyncio
async def test_execute_agent_turn_standalone():
    formatter = JSONFormatter()
    display = DisplayEngine(plain_mode=True)
    res = await execute_agent_turn(
        prompt="Hello world",
        session_id="sess_123",
        provider="local",
        model="heuristic",
        workspace=".",
        server_url="http://127.0.0.1:8000",
        formatter=formatter,
        quiet=True,
        display=display
    )
    assert res["success"] is True
    assert "Hello!" in res["response"] or "Processed" in res["response"] or "NeuroCore" in res["response"]
    assert "tokens_used" in res

@pytest.mark.asyncio
async def test_execute_agent_turn_list_files_prompt():
    formatter = JSONFormatter()
    display = DisplayEngine(plain_mode=True)
    res = await execute_agent_turn(
        prompt="list workspace files",
        session_id="sess_list",
        provider="local",
        model="heuristic",
        workspace=".",
        server_url="http://127.0.0.1:8000",
        formatter=formatter,
        quiet=True,
        display=display
    )
    assert res["success"] is True
    assert len(res["tool_calls"]) > 0
    assert res["tool_calls"][0]["name"] == "list_dir"

@pytest.mark.asyncio
async def test_execute_agent_turn_command_prompt():
    formatter = JSONFormatter()
    display = DisplayEngine(plain_mode=True)
    res = await execute_agent_turn(
        prompt="run shell command",
        session_id="sess_cmd",
        provider="local",
        model="heuristic",
        workspace=".",
        server_url="http://127.0.0.1:8000",
        formatter=formatter,
        quiet=True,
        display=display
    )
    assert res["success"] is True
    assert len(res["tool_calls"]) > 0
    assert res["tool_calls"][0]["name"] == "run_command"

@pytest.mark.asyncio
async def test_execute_agent_turn_write_diff_prompt():
    formatter = JSONFormatter()
    display = DisplayEngine(plain_mode=True)
    res = await execute_agent_turn(
        prompt="write file hello.txt with diff",
        session_id="sess_write",
        provider="local",
        model="heuristic",
        workspace=".",
        server_url="http://127.0.0.1:8000",
        formatter=formatter,
        quiet=True,
        display=display
    )
    assert res["success"] is True
    assert len(res["tool_calls"]) > 0
    assert res["tool_calls"][0]["name"] == "write_file"

# ============================================================================
# 3. Batch Mode Execution Tests (Tier 1 & Tier 2)
# ============================================================================

@pytest.mark.asyncio
async def test_batch_mode_json_output(capsys):
    config = CLIConfig(
        prompt="Run unit tests",
        output_format="json",
        provider="local"
    )
    display = DisplayEngine(plain_mode=True)
    exit_code = await run_non_interactive_batch(config, display)
    assert exit_code == 0

    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert data["success"] is True
    assert "response" in data
    assert "tool_calls" in data

@pytest.mark.asyncio
async def test_batch_mode_plain_output(capsys):
    config = CLIConfig(
        prompt="Explain recursion",
        output_format="plain",
        provider="local"
    )
    display = DisplayEngine(plain_mode=True)
    exit_code = await run_non_interactive_batch(config, display)
    assert exit_code == 0

    captured = capsys.readouterr()
    assert len(captured.out.strip()) > 0
    assert "\x1b[" not in captured.out

@pytest.mark.asyncio
async def test_batch_mode_rich_output():
    output_stream = io.StringIO()
    from rich.console import Console
    console = Console(file=output_stream, force_terminal=True)
    display = DisplayEngine(console=console)

    config = CLIConfig(
        prompt="Hi agent",
        output_format="rich",
        provider="local"
    )
    exit_code = await run_non_interactive_batch(config, display)
    assert exit_code == 0
    assert len(output_stream.getvalue()) > 0

@pytest.mark.asyncio
async def test_batch_mode_empty_prompt_returns_usage_code_2(capsys):
    config = CLIConfig(
        prompt="",
        output_format="plain",
        provider="local"
    )
    display = DisplayEngine(plain_mode=True)
    with patch("sys.stdin.isatty", return_value=True):
        exit_code = await run_non_interactive_batch(config, display)
        assert exit_code == 2

@pytest.mark.asyncio
async def test_batch_mode_piped_stdin(capsys):
    config = CLIConfig(
        prompt=None,
        output_format="json",
        provider="local"
    )
    display = DisplayEngine(plain_mode=True)
    mock_stdin = io.StringIO("Piped prompt content")
    with patch("sys.stdin", mock_stdin), patch("sys.stdin.isatty", return_value=False):
        exit_code = await run_non_interactive_batch(config, display)
        assert exit_code == 0

    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert data["success"] is True

@pytest.mark.asyncio
async def test_batch_mode_exception_handling():
    config = CLIConfig(
        prompt="Test exception handling",
        output_format="plain",
        provider="local"
    )
    display = DisplayEngine(plain_mode=True)
    with patch("neurocore.cli.main.execute_agent_turn", side_effect=RuntimeError("Simulated Failure")):
        exit_code = await run_non_interactive_batch(config, display)
        assert exit_code == 1

# ============================================================================
# 4. Interactive REPL Session Tests (Tier 1 & Tier 2)
# ============================================================================

@pytest.mark.asyncio
async def test_interactive_repl_slash_exit():
    output_stream = io.StringIO()
    from rich.console import Console
    console = Console(file=output_stream, force_terminal=True)
    display = DisplayEngine(console=console)

    config = CLIConfig(quiet=True)
    inputs = ["/status", "/exit"]

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
        assert "Session & System Status" in output_stream.getvalue()
        assert "Exiting NeuroCore Agent" in output_stream.getvalue()

@pytest.mark.asyncio
async def test_interactive_repl_eof_handling():
    display = DisplayEngine(plain_mode=True)
    config = CLIConfig(quiet=True)
    with patch("prompt_toolkit.PromptSession.prompt_async", side_effect=EOFError), \
         patch("builtins.input", side_effect=EOFError):
        exit_code = await run_interactive_repl(config, display)
        assert exit_code == 0

@pytest.mark.asyncio
async def test_interactive_repl_keyboard_interrupt():
    display = DisplayEngine(plain_mode=True)
    config = CLIConfig(quiet=True)
    inputs = [KeyboardInterrupt(), "/exit"]

    def mock_input(prompt=""):
        res = inputs.pop(0)
        if isinstance(res, Exception):
            raise res
        return res

    with patch("builtins.input", side_effect=mock_input), \
         patch("prompt_toolkit.PromptSession.prompt_async", side_effect=mock_input):
        exit_code = await run_interactive_repl(config, display)
        assert exit_code == 0

# ============================================================================
# 5. CLI Runner & Main Entrypoint Tests (Tier 1 & Tier 2)
# ============================================================================

def test_cli_runner_class():
    runner = CLIRunner()
    assert runner.display is not None

    exit_code = asyncio.run(runner.run_batch("Hello", "json", "local", "."))
    assert exit_code == 0

def test_main_entry_point():
    test_args = ["neurocore", "-p", "Hello test", "-o", "json"]
    with patch.object(sys, "argv", test_args):
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 0
