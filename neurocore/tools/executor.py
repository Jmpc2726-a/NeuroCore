"""
Tool Registry and Execution Dispatcher for NeuroCore Agent.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from neurocore.tools.base import BaseTool, ToolResult
from neurocore.tools.file_tools import (
    ReadFileTool,
    ListDirTool,
    GrepSearchTool,
    WriteFileTool,
    ReplaceFileContentTool,
)
from neurocore.tools.shell_tools import RunCommandTool


class ToolRegistry:
    """
    Central registry mapping tool names to tool handlers.
    """

    def __init__(self):
        self._tools: Dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> None:
        self._tools[tool.name] = tool

    def get_tool(self, name: str) -> Optional[BaseTool]:
        return self._tools.get(name)

    def list_tools(self) -> List[BaseTool]:
        return list(self._tools.values())

    def get_tools_manifest(self) -> Dict[str, Any]:
        return {name: tool.get_json_schema() for name, tool in self._tools.items()}

    def get_tool_definitions(self) -> List[Dict[str, Any]]:
        return [tool.get_json_schema() for tool in self._tools.values()]


def create_default_registry(workspace_root: Optional[Union[Path, str]] = None) -> ToolRegistry:
    registry = ToolRegistry()
    ws = Path(workspace_root).resolve() if workspace_root else None
    registry.register(ReadFileTool(workspace_root=ws))
    registry.register(ListDirTool(workspace_root=ws))
    registry.register(GrepSearchTool(workspace_root=ws))
    registry.register(WriteFileTool(workspace_root=ws))
    registry.register(ReplaceFileContentTool(workspace_root=ws))
    registry.register(RunCommandTool(workspace_root=ws))
    return registry


class ToolExecutor:
    """
    Dispatcher executing tools by name with timing and error handling.
    """

    def __init__(
        self,
        registry: Optional[ToolRegistry] = None,
        workspace_root: Optional[Union[Path, str]] = None,
    ):
        if registry is not None:
            self.registry = registry
        else:
            self.registry = create_default_registry(workspace_root=workspace_root)
        self.workspace_root = workspace_root or "."

    @property
    def tools(self) -> Dict[str, BaseTool]:
        return self.registry._tools

    def get_tool_definitions(self) -> List[Dict[str, Any]]:
        return self.registry.get_tool_definitions()

    def get_tools_manifest(self) -> Dict[str, Any]:
        return self.registry.get_tools_manifest()

    async def execute(self, tool_call_id: str, name: str, arguments: Dict[str, Any]) -> ToolResult:
        start_time = time.time()
        tool = self.registry.get_tool(name)

        if not tool:
            return ToolResult(
                tool_call_id=tool_call_id,
                name=name,
                status="error",
                output="",
                error=f"Unknown tool '{name}'",
                execution_time_ms=int((time.time() - start_time) * 1000),
            )

        try:
            if hasattr(tool, "execute"):
                res = await tool.execute(tool_call_id=tool_call_id, **arguments)
            elif hasattr(tool, "run_async"):
                res = await tool.run_async(tool_call_id=tool_call_id, **arguments)
            else:
                res = tool.run(**arguments)
            res.tool_call_id = tool_call_id
            return res
        except Exception as exc:
            return ToolResult(
                tool_call_id=tool_call_id,
                name=name,
                status="error",
                output="",
                error=f"Unhandled tool exception: {str(exc)}",
                execution_time_ms=int((time.time() - start_time) * 1000),
            )
