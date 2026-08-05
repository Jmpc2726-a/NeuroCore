"""
Sandboxed shell execution tool for NeuroCore Agent.
"""

from __future__ import annotations

import asyncio
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, Optional

from neurocore.config import get_settings
from neurocore.tools.base import BaseTool, ToolResult


class RunCommandTool(BaseTool):
    name = "run_command"
    description = "Execute a shell command within the workspace directory asynchronously with timeout enforcement."
    parameters_schema = {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "Shell command line to execute"},
            "cwd": {"type": "string", "description": "Working directory relative to workspace root (defaults to workspace root)"},
            "timeout": {"type": "integer", "description": "Command execution timeout in seconds (default: 30)"},
        },
        "required": ["command"],
    }

    FORBIDDEN_PATTERNS = [
        r"rm\s+-rf\s+/(?:\s|$)",
        r"mkfs",
        r"dd\s+if=/dev/zero",
        r":\(\)\{\s*:\|:&\s*\};:",  # Fork bomb
        r"chmod\s+-R\s+777\s+/(?:\s|$)",
        r">\s*/dev/sd[a-z]",
    ]

    def _run_sync(self, tool_call_id: str = "", **kwargs: Any) -> ToolResult:
        command = kwargs.get("command", "")
        cwd_str = kwargs.get("cwd")
        timeout_sec = kwargs.get("timeout") or get_settings().shell_timeout

        for pattern in self.FORBIDDEN_PATTERNS:
            if re.search(pattern, command):
                return ToolResult(
                    tool_call_id=tool_call_id,
                    name=self.name,
                    status="error",
                    error=f"Prohibited/blocked command execution attempt: matches dangerous pattern '{pattern}'",
                )

        target_cwd = self.validate_path(cwd_str) if cwd_str else self.workspace_root

        if not target_cwd.exists() or not target_cwd.is_dir():
            return ToolResult(
                tool_call_id=tool_call_id,
                name=self.name,
                status="error",
                error=f"Invalid working directory: {cwd_str}",
            )

        try:
            res = subprocess.run(
                command,
                shell=True,
                cwd=str(target_cwd),
                capture_output=True,
                text=True,
                timeout=float(timeout_sec),
            )
            stdout_str = res.stdout
            stderr_str = res.stderr
            returncode = res.returncode
        except subprocess.TimeoutExpired:
            return ToolResult(
                tool_call_id=tool_call_id,
                name=self.name,
                status="error",
                error=f"Command execution timed out after {timeout_sec} seconds",
            )
        except Exception as exc:
            return ToolResult(
                tool_call_id=tool_call_id,
                name=self.name,
                status="error",
                error=str(exc),
            )

        max_output_len = 50000
        if len(stdout_str) > max_output_len:
            stdout_str = stdout_str[:max_output_len] + "\n... [Truncated stdout]"
        if len(stderr_str) > max_output_len:
            stderr_str = stderr_str[:max_output_len] + "\n... [Truncated stderr]"

        status = "success" if returncode == 0 else "error"
        error_msg = None if returncode == 0 else f"Command failed with exit code {returncode}"

        return ToolResult(
            tool_call_id=tool_call_id,
            name=self.name,
            status=status,
            output={
                "stdout": stdout_str,
                "stderr": stderr_str,
                "exit_code": returncode,
                "command": command,
            },
            error=error_msg,
        )

    async def _run_async_impl(self, tool_call_id: str = "", **kwargs: Any) -> ToolResult:
        command = kwargs.get("command", "")
        cwd_str = kwargs.get("cwd")
        timeout_sec = kwargs.get("timeout") or get_settings().shell_timeout

        for pattern in self.FORBIDDEN_PATTERNS:
            if re.search(pattern, command):
                return ToolResult(
                    tool_call_id=tool_call_id,
                    name=self.name,
                    status="error",
                    error=f"Prohibited/blocked command execution attempt: matches dangerous pattern '{pattern}'",
                )

        target_cwd = self.validate_path(cwd_str) if cwd_str else self.workspace_root

        if not target_cwd.exists() or not target_cwd.is_dir():
            return ToolResult(
                tool_call_id=tool_call_id,
                name=self.name,
                status="error",
                error=f"Invalid working directory: {cwd_str}",
            )

        process = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(target_cwd),
        )

        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                process.communicate(), timeout=float(timeout_sec)
            )
        except asyncio.TimeoutError:
            try:
                process.kill()
                await process.wait()
            except Exception:
                pass
            return ToolResult(
                tool_call_id=tool_call_id,
                name=self.name,
                status="error",
                error=f"Command execution timed out after {timeout_sec} seconds",
            )

        stdout_str = stdout_bytes.decode("utf-8", errors="replace")
        stderr_str = stderr_bytes.decode("utf-8", errors="replace")

        max_output_len = 50000
        if len(stdout_str) > max_output_len:
            stdout_str = stdout_str[:max_output_len] + "\n... [Truncated stdout]"
        if len(stderr_str) > max_output_len:
            stderr_str = stderr_str[:max_output_len] + "\n... [Truncated stderr]"

        status = "success" if process.returncode == 0 else "error"
        error_msg = None if process.returncode == 0 else f"Command failed with exit code {process.returncode}"

        return ToolResult(
            tool_call_id=tool_call_id,
            name=self.name,
            status=status,
            output={
                "stdout": stdout_str,
                "stderr": stderr_str,
                "exit_code": process.returncode,
                "command": command,
            },
            error=error_msg,
        )
