"""
Sandboxed shell execution tool for NeuroCore Agent.

Atenção: executar comandos de shell é, por natureza, acesso total ao
servidor. Por isso esta ferramenta só é registrada quando
NEUROCORE_ENABLE_DANGEROUS_TOOLS está ativo (padrão apenas em uso local).
Além disso, os comandos rodam com um ambiente sem segredos (chaves de API,
tokens e senhas não são repassados ao processo filho).
"""

from __future__ import annotations

import asyncio
import os
import re
import subprocess
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from neurocore.config import get_settings
from neurocore.tools.base import BaseTool, ToolResult

MAX_OUTPUT_LEN = 50000
MAX_TIMEOUT_SECONDS = 300
_SECRET_ENV_RE = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|AUTH|COOKIE|SESSION)", re.IGNORECASE)


def sanitized_env() -> Dict[str, str]:
    """Cópia do ambiente atual sem variáveis que parecem conter segredos."""
    return {k: v for k, v in os.environ.items() if not _SECRET_ENV_RE.search(k)}


def _truncate(text: str, label: str) -> str:
    if len(text) > MAX_OUTPUT_LEN:
        return text[:MAX_OUTPUT_LEN] + f"\n... [Truncated {label}]"
    return text


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
        r"rm\s+-rf\s+~",
        r"mkfs",
        r"dd\s+if=/dev/zero",
        r":\(\)\s*\{\s*:\|:&\s*\};:",  # Fork bomb
        r"chmod\s+-R\s+777\s+/(?:\s|$)",
        r">\s*/dev/sd[a-z]",
        r"\bshutdown\b",
        r"\breboot\b",
    ]

    def _prepare(self, tool_call_id: str, kwargs: Dict[str, Any]) -> Tuple[Optional[ToolResult], str, Path, float]:
        command = str(kwargs.get("command") or "").strip()
        cwd_str = kwargs.get("cwd")
        try:
            timeout_sec = float(kwargs.get("timeout") or get_settings().shell_timeout)
        except (TypeError, ValueError):
            timeout_sec = float(get_settings().shell_timeout)
        timeout_sec = max(1.0, min(timeout_sec, float(MAX_TIMEOUT_SECONDS)))

        def fail(msg: str) -> Tuple[ToolResult, str, Path, float]:
            return ToolResult(tool_call_id=tool_call_id, name=self.name, status="error", error=msg), command, self.workspace_root, timeout_sec

        if not command:
            return fail("Empty command")

        for pattern in self.FORBIDDEN_PATTERNS:
            if re.search(pattern, command):
                return fail(f"Prohibited/blocked command execution attempt: matches dangerous pattern '{pattern}'")

        target_cwd = self.validate_path(cwd_str) if cwd_str else self.workspace_root
        if not target_cwd.exists() or not target_cwd.is_dir():
            return fail(f"Invalid working directory: {cwd_str}")
        return None, command, target_cwd, timeout_sec

    def _result(self, tool_call_id: str, command: str, stdout: str, stderr: str, code: int) -> ToolResult:
        return ToolResult(
            tool_call_id=tool_call_id,
            name=self.name,
            status="success" if code == 0 else "error",
            output={
                "stdout": _truncate(stdout, "stdout"),
                "stderr": _truncate(stderr, "stderr"),
                "exit_code": code,
                "command": command,
            },
            error=None if code == 0 else f"Command failed with exit code {code}",
        )

    def _run_sync(self, tool_call_id: str = "", **kwargs: Any) -> ToolResult:
        error, command, target_cwd, timeout_sec = self._prepare(tool_call_id, kwargs)
        if error:
            return error
        try:
            res = subprocess.run(
                command,
                shell=True,
                cwd=str(target_cwd),
                capture_output=True,
                text=True,
                timeout=timeout_sec,
                env=sanitized_env(),
                stdin=subprocess.DEVNULL,
            )
        except subprocess.TimeoutExpired:
            return ToolResult(tool_call_id=tool_call_id, name=self.name, status="error",
                              error=f"Command execution timed out after {int(timeout_sec)} seconds")
        except Exception as exc:
            return ToolResult(tool_call_id=tool_call_id, name=self.name, status="error", error=str(exc))
        return self._result(tool_call_id, command, res.stdout, res.stderr, res.returncode)

    async def _run_async_impl(self, tool_call_id: str = "", **kwargs: Any) -> ToolResult:
        error, command, target_cwd, timeout_sec = self._prepare(tool_call_id, kwargs)
        if error:
            return error

        process = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            stdin=asyncio.subprocess.DEVNULL,
            cwd=str(target_cwd),
            env=sanitized_env(),
        )
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(process.communicate(), timeout=timeout_sec)
        except asyncio.TimeoutError:
            try:
                process.kill()
                await process.wait()
            except Exception:
                pass
            return ToolResult(tool_call_id=tool_call_id, name=self.name, status="error",
                              error=f"Command execution timed out after {int(timeout_sec)} seconds")

        return self._result(
            tool_call_id,
            command,
            stdout_bytes.decode("utf-8", errors="replace"),
            stderr_bytes.decode("utf-8", errors="replace"),
            process.returncode if process.returncode is not None else -1,
        )
