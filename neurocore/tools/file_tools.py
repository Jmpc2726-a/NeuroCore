"""
Sandboxed file operation tools for NeuroCore Agent.
"""

from __future__ import annotations

import os
import re
import time
import difflib
from pathlib import Path
from typing import Any, Dict, List, Optional

from neurocore.config import get_settings
from neurocore.tools.base import BaseTool, ToolResult, is_protected_path


BINARY_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".tiff", ".webp",
    ".pdf", ".zip", ".tar", ".gz", ".7z", ".rar", ".bz2",
    ".pyc", ".pyo", ".so", ".o", ".a", ".dylib", ".dll", ".exe", ".bin",
    ".mp3", ".mp4", ".wav", ".avi", ".mov", ".flv", ".db", ".sqlite"
}


class ReadFileTool(BaseTool):
    name = "read_file"
    description = "Read text content from a file within the workspace, optionally specifying start and end line ranges."
    parameters_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Relative or absolute path to the file inside workspace"},
            "start_line": {"type": "integer", "description": "1-indexed starting line number (optional)"},
            "end_line": {"type": "integer", "description": "1-indexed ending line number (optional)"},
        },
        "required": ["path"],
    }

    def _run_sync(self, tool_call_id: str = "", **kwargs: Any) -> ToolResult:
        path_str = kwargs.get("path", "")
        start_line = kwargs.get("start_line")
        end_line = kwargs.get("end_line")

        try:
            target_path = self.validate_path(path_str)
            if not target_path.exists():
                return ToolResult(
                    tool_call_id=tool_call_id,
                    name=self.name,
                    status="error",
                    error=f"File not found: {path_str}",
                )
            if not target_path.is_file():
                return ToolResult(
                    tool_call_id=tool_call_id,
                    name=self.name,
                    status="error",
                    error=f"Path is a directory, not a file: {path_str}",
                )

            if target_path.suffix.lower() in BINARY_EXTENSIONS:
                return ToolResult(
                    tool_call_id=tool_call_id,
                    name=self.name,
                    status="error",
                    error=f"Binary file reading is prohibited: {path_str}",
                )

            max_size = get_settings().max_file_size_bytes
            file_size = target_path.stat().st_size
            if file_size > max_size:
                return ToolResult(
                    tool_call_id=tool_call_id,
                    name=self.name,
                    status="error",
                    error=f"File size ({file_size} bytes) exceeds limit ({max_size} bytes)",
                )

            with open(target_path, mode="r", encoding="utf-8") as f:
                lines = f.readlines()

            if start_line is not None or end_line is not None:
                total_lines = len(lines)
                s = (start_line - 1) if start_line and start_line > 0 else 0
                e = end_line if end_line and end_line > 0 else total_lines
                sliced_lines = lines[s:e]
                formatted = []
                for idx, line in enumerate(sliced_lines, start=s + 1):
                    formatted.append(f"{idx}: {line}")
                content = "".join(formatted)
            else:
                content = "".join(lines)

            return ToolResult(
                tool_call_id=tool_call_id,
                name=self.name,
                status="success",
                output=content,
            )
        except UnicodeDecodeError:
            return ToolResult(
                tool_call_id=tool_call_id,
                name=self.name,
                status="error",
                error=f"Binary file reading is prohibited: failed UTF-8 decode for {path_str}",
            )
        except Exception as exc:
            return ToolResult(
                tool_call_id=tool_call_id,
                name=self.name,
                status="error",
                error=str(exc),
            )


class ListDirTool(BaseTool):
    name = "list_dir"
    description = "List files and subdirectories within a specified directory in the workspace."
    parameters_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Directory path to list (defaults to '.')"},
        },
        "required": [],
    }

    def _run_sync(self, tool_call_id: str = "", **kwargs: Any) -> ToolResult:
        path_str = kwargs.get("path", ".")

        try:
            target_path = self.validate_path(path_str)
            if not target_path.exists():
                return ToolResult(
                    tool_call_id=tool_call_id,
                    name=self.name,
                    status="error",
                    error=f"Directory not found: {path_str}",
                )
            if not target_path.is_dir():
                return ToolResult(
                    tool_call_id=tool_call_id,
                    name=self.name,
                    status="error",
                    error=f"Path is not a directory: {path_str}",
                )

            items = []
            for child in target_path.iterdir():
                if is_protected_path(child, self.workspace_root):
                    continue
                try:
                    stat = child.stat()
                except OSError:
                    continue
                mod_time = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(stat.st_mtime))
                items.append({
                    "name": child.name,
                    "type": "directory" if child.is_dir() else "file",
                    "size": stat.st_size if child.is_file() else 0,
                    "modified_time": mod_time,
                })

            items.sort(key=lambda x: (x["type"] != "directory", x["name"]))

            return ToolResult(
                tool_call_id=tool_call_id,
                name=self.name,
                status="success",
                output=items,
            )
        except Exception as exc:
            return ToolResult(
                tool_call_id=tool_call_id,
                name=self.name,
                status="error",
                error=str(exc),
            )


class GrepSearchTool(BaseTool):
    name = "grep_search"
    description = "Search for a string pattern or regular expression across text files in the workspace."
    parameters_schema = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "Search string pattern or regular expression"},
            "path": {"type": "string", "description": "Search directory path (defaults to '.')"},
            "is_regex": {"type": "boolean", "description": "Whether to evaluate pattern as regex"},
            "case_sensitive": {"type": "boolean", "description": "Whether search is case-sensitive (default: true)"},
        },
        "required": ["pattern"],
    }

    IGNORE_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", ".sessions", ".tasks", ".uploads", ".agents", ".pytest_cache", ".egg-info", ".cache_enem"}
    MAX_MATCHES = 500
    MAX_FILES = 5000

    def _run_sync(self, tool_call_id: str = "", **kwargs: Any) -> ToolResult:
        pattern = kwargs.get("pattern", "")
        path_str = kwargs.get("path", ".")
        is_regex = kwargs.get("is_regex", False)
        case_sensitive = kwargs.get("case_sensitive", True)

        try:
            target_path = self.validate_path(path_str)
            if not target_path.exists():
                return ToolResult(
                    tool_call_id=tool_call_id,
                    name=self.name,
                    status="error",
                    error=f"Search path not found: {path_str}",
                )

            flags = 0 if case_sensitive else re.IGNORECASE
            if is_regex:
                regex = re.compile(pattern, flags)
            else:
                regex = re.compile(re.escape(pattern), flags)

            matches: List[Dict[str, Any]] = []
            files_to_scan: List[Path] = []
            ws = self.workspace_root.resolve()

            if target_path.is_file():
                files_to_scan.append(target_path)
            else:
                for root, dirs, files in os.walk(target_path):
                    dirs[:] = [d for d in dirs if d not in self.IGNORE_DIRS]
                    for file_name in files:
                        files_to_scan.append(Path(root) / file_name)

            for file_path in files_to_scan[: self.MAX_FILES]:
                if len(matches) >= self.MAX_MATCHES:
                    break
                if is_protected_path(file_path, ws) or file_path.suffix.lower() in BINARY_EXTENSIONS:
                    continue
                try:
                    if file_path.stat().st_size > get_settings().max_file_size_bytes:
                        continue
                    rel_path = str(file_path.relative_to(ws))
                    with open(file_path, mode="r", encoding="utf-8", errors="ignore") as f:
                        line_idx = 1
                        for line in f:
                            if regex.search(line):
                                matches.append({
                                    "file": rel_path,
                                    "line_number": line_idx,
                                    "line_content": line.rstrip("\r\n")[:400],
                                })
                            line_idx += 1
                except Exception:
                    continue

            return ToolResult(
                tool_call_id=tool_call_id,
                name=self.name,
                status="success",
                output=matches,
            )
        except Exception as exc:
            return ToolResult(
                tool_call_id=tool_call_id,
                name=self.name,
                status="error",
                error=str(exc),
            )


class WriteFileTool(BaseTool):
    name = "write_file"
    description = "Write content to a file in the workspace, creating parent directories if needed."
    parameters_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Target file path relative to workspace"},
            "content": {"type": "string", "description": "Text content to write"},
            "overwrite": {"type": "boolean", "description": "Overwrite file if it already exists (default: true)"},
        },
        "required": ["path", "content"],
    }

    def _run_sync(self, tool_call_id: str = "", **kwargs: Any) -> ToolResult:
        path_str = kwargs.get("path", "")
        content = kwargs.get("content", "")
        overwrite = kwargs.get("overwrite", True)

        try:
            target_path = self.validate_path(path_str)

            if target_path.exists() and not overwrite:
                return ToolResult(
                    tool_call_id=tool_call_id,
                    name=self.name,
                    status="error",
                    error=f"File already exists and overwrite=False: {path_str}",
                )

            target_path.parent.mkdir(parents=True, exist_ok=True)

            with open(target_path, mode="w", encoding="utf-8") as f:
                f.write(content)

            ws = self.workspace_root.resolve()
            rel_path = str(target_path.relative_to(ws))
            lines = content.splitlines()
            diff_header = [f"--- a/{rel_path}\n", f"+++ b/{rel_path}\n", f"@@ -0,0 +1,{max(1, len(lines))} @@\n"]
            diff_header.extend(f"+{line}\n" for line in lines)
            diff_str = "".join(diff_header)

            return ToolResult(
                tool_call_id=tool_call_id,
                name=self.name,
                status="success",
                output={
                    "path": rel_path,
                    "bytes_written": len(content.encode("utf-8")),
                    "diff": diff_str,
                    "status": "written",
                },
            )
        except Exception as exc:
            return ToolResult(
                tool_call_id=tool_call_id,
                name=self.name,
                status="error",
                error=str(exc),
            )


class ReplaceFileContentTool(BaseTool):
    name = "replace_file_content"
    description = "Replace an exact target content string with replacement content in a file and return unified diff."
    parameters_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File path relative to workspace"},
            "target_content": {"type": "string", "description": "Exact text string to be replaced"},
            "replacement_content": {"type": "string", "description": "New replacement text string"},
        },
        "required": ["path", "target_content", "replacement_content"],
    }

    def _run_sync(self, tool_call_id: str = "", **kwargs: Any) -> ToolResult:
        path_str = kwargs.get("path", "")
        old_content = kwargs.get("target_content", kwargs.get("old_content", ""))
        new_content = kwargs.get("replacement_content", kwargs.get("new_content", ""))

        try:
            target_path = self.validate_path(path_str)

            if not target_path.exists():
                return ToolResult(
                    tool_call_id=tool_call_id,
                    name=self.name,
                    status="error",
                    error=f"File not found: {path_str}",
                )

            with open(target_path, mode="r", encoding="utf-8") as f:
                current_text = f.read()

            occurrences = current_text.count(old_content)
            if occurrences == 0:
                return ToolResult(
                    tool_call_id=tool_call_id,
                    name=self.name,
                    status="error",
                    error="Target content not found in file",
                )
            if occurrences > 1:
                return ToolResult(
                    tool_call_id=tool_call_id,
                    name=self.name,
                    status="error",
                    error="Target content found multiple times in file; expected unique match",
                )

            updated_text = current_text.replace(old_content, new_content, 1)

            diff_lines = list(
                difflib.unified_diff(
                    current_text.splitlines(keepends=True),
                    updated_text.splitlines(keepends=True),
                    fromfile=f"a/{path_str}",
                    tofile=f"b/{path_str}",
                )
            )
            diff_str = "".join(diff_lines)

            with open(target_path, mode="w", encoding="utf-8") as f:
                f.write(updated_text)

            ws = self.workspace_root.resolve()
            rel_path = str(target_path.relative_to(ws))
            return ToolResult(
                tool_call_id=tool_call_id,
                name=self.name,
                status="success",
                output={
                    "path": rel_path,
                    "diff": diff_str,
                    "status": "replaced",
                },
            )
        except Exception as exc:
            return ToolResult(
                tool_call_id=tool_call_id,
                name=self.name,
                status="error",
                error=str(exc),
            )
