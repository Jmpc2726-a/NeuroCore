#!/usr/bin/env python3
"""
Standalone Verification Script for NeuroCore Interactive Terminal CLI.
"""

import sys
import os
import subprocess
import json

# Ensure project root is in PYTHONPATH
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from neurocore.cli.display import DisplayEngine, SlashCommandHandler, JSONFormatter
from neurocore.cli.main import create_argument_parser, CLIConfig


def run_cli_subprocess(args: list[str], input_str: str = None) -> tuple[int, str, str]:
    """Runs python3 -m neurocore.cli with given CLI arguments."""
    env = os.environ.copy()
    env["PYTHONPATH"] = PROJECT_ROOT
    cmd = [sys.executable, "-m", "neurocore.cli"] + args
    res = subprocess.run(
        cmd,
        input=input_str,
        text=True,
        capture_output=True,
        cwd=PROJECT_ROOT,
        env=env
    )
    return res.returncode, res.stdout, res.stderr


def verify_batch_json():
    print("Checking 1: Non-interactive batch mode (--output json)...")
    code, stdout, stderr = run_cli_subprocess(["-p", "Hello agent", "--output", "json"])
    assert code == 0, f"Expected exit code 0, got {code}. Stderr: {stderr}"
    data = json.loads(stdout)
    assert data.get("success") is True, f"Expected success: true in json payload: {data}"
    assert "response" in data, "Expected response key in json payload"
    print("  ✓ JSON batch mode verified successfully.")


def verify_batch_plain():
    print("Checking 2: Non-interactive batch mode (--output plain)...")
    code, stdout, stderr = run_cli_subprocess(["-p", "Hello agent", "--output", "plain"])
    assert code == 0, f"Expected exit code 0, got {code}. Stderr: {stderr}"
    assert len(stdout.strip()) > 0, "Expected non-empty plain output"
    assert "\x1b[" not in stdout, "Plain output should not contain ANSI escape sequences"
    print("  ✓ Plain batch mode verified successfully.")


def verify_batch_rich():
    print("Checking 3: Non-interactive batch mode (--output rich)...")
    code, stdout, stderr = run_cli_subprocess(["-p", "Hello agent", "--output", "rich"])
    assert code == 0, f"Expected exit code 0, got {code}. Stderr: {stderr}"
    assert len(stdout.strip()) > 0, "Expected non-empty rich output"
    print("  ✓ Rich batch mode verified successfully.")


def verify_batch_tool_traces():
    print("Checking 4: Tool execution traces and diffs...")
    code, stdout, stderr = run_cli_subprocess(["-p", "write diff file", "--output", "rich"])
    assert code == 0, f"Expected exit code 0, got {code}. Stderr: {stderr}"
    assert "write_file Results" in stdout or "status" in stdout or "written" in stdout or "Code Diff" in stdout, "Expected tool execution trace in output"

    code_cmd, stdout_cmd, _ = run_cli_subprocess(["-p", "run command echo test", "--output", "rich"])
    assert code_cmd == 0, f"Expected exit code 0, got {code_cmd}"
    assert "Shell Command Execution" in stdout_cmd or "SUCCESS" in stdout_cmd, "Expected command execution trace"
    print("  ✓ Tool execution traces verified successfully.")


def verify_usage_error():
    print("Checking 5: Usage error handling (empty prompt)...")
    code, stdout, stderr = run_cli_subprocess(["-p", "", "--output", "json"])
    assert code == 2, f"Expected exit code 2 for empty prompt usage error, got {code}"
    print("  ✓ Usage error handling verified successfully.")


def verify_slash_commands():
    print("Checking 6: Interactive slash commands engine...")
    import asyncio
    engine = DisplayEngine(plain_mode=True)
    handler = SlashCommandHandler(engine)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    res_help = loop.run_until_complete(handler.handle_command("/help", "", "local", "heuristic", ".", 0, "s1"))
    assert res_help["action"] == "help"

    res_prov = loop.run_until_complete(handler.handle_command("/provider", "openai", "local", "heuristic", ".", 0, "s1"))
    assert res_prov["action"] == "provider_changed"
    assert res_prov["new_provider"] == "openai"

    res_stat = loop.run_until_complete(handler.handle_command("/status", "", "openai", "gpt-4o", ".", 100, "s1"))
    assert res_stat["action"] == "status"

    res_reset = loop.run_until_complete(handler.handle_command("/reset", "", "openai", "gpt-4o", ".", 100, "s1"))
    assert res_reset["action"] == "reset"

    res_exit = loop.run_until_complete(handler.handle_command("/exit", "", "openai", "gpt-4o", ".", 100, "s1"))
    assert res_exit["action"] == "exit"

    loop.close()
    print("  ✓ Slash commands engine verified successfully.")


def main():
    print("==========================================================")
    print("NeuroCore Interactive Terminal CLI Verification Suite")
    print("==========================================================")

    try:
        verify_batch_json()
        verify_batch_plain()
        verify_batch_rich()
        verify_batch_tool_traces()
        verify_usage_error()
        verify_slash_commands()
        print("\n==========================================================")
        print("ALL VERIFICATION CHECKS PASSED SUCCESSFULLY! [100% OK]")
        print("==========================================================")
        sys.exit(0)
    except AssertionError as err:
        print(f"\n❌ VERIFICATION FAILURE: {err}", file=sys.stderr)
        sys.exit(1)
    except Exception as exc:
        print(f"\n❌ UNEXPECTED ERROR: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
