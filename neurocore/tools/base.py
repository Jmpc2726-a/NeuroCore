"""
Base tool interfaces and ToolResult model for NeuroCore Agent.
"""

from __future__ import annotations

import os
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, List, Optional, Union
from pydantic import BaseModel, Field

from neurocore.config import get_settings


class ToolResult(BaseModel):
    tool_call_id: str = ""
    name: str = ""
    status: str = "success"  # "success" | "error"
    output: Union[str, Dict[str, Any], List[Any]] = ""
    error: Optional[str] = None
    execution_time_ms: int = 0

ToolResult.model_rebuild()


# Caminhos que nenhuma ferramenta pode ler ou alterar, mesmo dentro do
# workspace: segredos (.env), histórico de conversas e metadados do git.
PROTECTED_DIR_NAMES = {".git", ".sessions", ".tasks"}
PROTECTED_FILE_PREFIXES = (".env",)
PROTECTED_FILE_NAMES = {".tasks.json", ".netrc", ".pypirc", "id_rsa", "id_ed25519"}


def is_protected_path(path: Path, workspace_root: Path) -> bool:
    try:
        rel = path.resolve().relative_to(workspace_root.resolve())
    except ValueError:
        return True
    for part in rel.parts:
        if part in PROTECTED_DIR_NAMES or part in PROTECTED_FILE_NAMES:
            return True
        if part.startswith(PROTECTED_FILE_PREFIXES) and part != ".env.example":
            return True
    return False


class BaseTool(ABC):
    name: str = ""
    description: str = ""
    parameters_schema: Dict[str, Any] = Field(default_factory=dict)

    def __init__(self, workspace_root: Optional[Union[str, Path]] = None):
        if workspace_root is not None:
            self.workspace_root = Path(workspace_root).resolve()
        else:
            self.workspace_root = get_settings().workspace_root.resolve()

    @property
    def parameters(self) -> Dict[str, Any]:
        return getattr(self, "parameters_schema", {})

    def validate_path(self, path_str: Optional[str] = None) -> Path:
        """
        Validate and resolve target path ensuring it remains strictly inside workspace_root.
        Prevents directory traversal attacks.
        """
        ws = self.workspace_root.resolve()
        if not path_str or path_str == ".":
            return ws

        p = Path(path_str)
        if p.is_absolute():
            target = p.resolve()
        else:
            target = (ws / p).resolve()

        try:
            target.relative_to(ws)
        except ValueError:
            raise ValueError(f"Access denied: path '{path_str}' is outside the workspace root")

        if is_protected_path(target, ws):
            raise ValueError(f"Access denied: path '{path_str}' is protected (secrets, sessions or git metadata)")

        return target

    def _resolve_path(self, path_str: str) -> str:
        return str(self.validate_path(path_str))

    def get_definition(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
        }

    def get_json_schema(self) -> Dict[str, Any]:
        return self.get_definition()

    def run(self, tool_call_id: str = "", **kwargs: Any) -> ToolResult:
        """
        Synchronous execution entrypoint.
        """
        start_time = time.time()
        try:
            res = self._run_sync(tool_call_id=tool_call_id, **kwargs)
            res.execution_time_ms = int((time.time() - start_time) * 1000)
            return res
        except Exception as exc:
            return ToolResult(
                tool_call_id=tool_call_id,
                name=self.name,
                status="error",
                error=str(exc),
                execution_time_ms=int((time.time() - start_time) * 1000),
            )

    async def run_async(self, tool_call_id: str = "", **kwargs: Any) -> ToolResult:
        """
        Asynchronous execution entrypoint delegating to execute.
        """
        return await self.execute(tool_call_id=tool_call_id, **kwargs)

    async def execute(self, tool_call_id: str = "", **kwargs: Any) -> ToolResult:
        """
        Asynchronous execution entrypoint.
        """
        start_time = time.time()
        try:
            res = await self._run_async_impl(tool_call_id=tool_call_id, **kwargs)
            res.execution_time_ms = int((time.time() - start_time) * 1000)
            return res
        except Exception as exc:
            return ToolResult(
                tool_call_id=tool_call_id,
                name=self.name,
                status="error",
                error=str(exc),
                execution_time_ms=int((time.time() - start_time) * 1000),
            )

    def _run_sync(self, tool_call_id: str = "", **kwargs: Any) -> ToolResult:
        raise NotImplementedError

    async def _run_async_impl(self, tool_call_id: str = "", **kwargs: Any) -> ToolResult:
        return self._run_sync(tool_call_id=tool_call_id, **kwargs)
