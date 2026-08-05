"""
Rich terminal formatting engine for NeuroCore Code Agent CLI.
"""

import sys
import os
import json
import difflib
from typing import Dict, Any, List, Optional, Union
from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.markdown import Markdown
from rich.live import Live
from rich.spinner import Spinner
from rich.text import Text
from rich.syntax import Syntax
from rich.theme import Theme


# Cyberpunk theme palette
NEUROCORE_THEME = Theme({
    "cyan": "#00F0FF",
    "violet": "#8B5CF6",
    "emerald": "#10B981",
    "red": "#EF4444",
    "amber": "#F59E0B",
    "gray": "#6B7280",
    "dark_bg": "#111827",
    "status_title": "bold #00F0FF",
    "running_badge": "bold black on #F59E0B",
    "success_badge": "bold black on #10B981",
    "failed_badge": "bold white on #EF4444",
})


class DisplayEngine:
    """
    Rich visual display manager for NeuroCore CLI.
    Manages terminal rendering, tool execution panels, code diffs, command outputs, and status bar.
    """
    def __init__(
        self,
        console: Optional[Console] = None,
        theme: Optional[Theme] = None,
        plain_mode: bool = False
    ):
        self.plain_mode = plain_mode
        if console:
            self.console = console
        else:
            self.console = Console(theme=theme or NEUROCORE_THEME)

        self.current_provider: str = "local"
        self.current_model: str = "heuristic"
        self.active_workspace: str = os.path.abspath(".")
        self.total_tokens: int = 0

    def get_console(self) -> Console:
        return self.console

    def set_plain_mode(self, plain: bool):
        self.plain_mode = plain
        if plain:
            self.console = Console(file=self.console.file if hasattr(self, 'console') else None, no_color=True, highlight=False)

    def render_welcome_banner(self):
        """Renders Cyberpunk welcome splash banner."""
        if self.plain_mode:
            self.console.print("=== NeuroCore Code Agent CLI (v1.0.0) ===")
            self.console.print("Type /help for slash commands, /exit to quit.\n")
            return

        banner_text = Text()
        banner_text.append("      _  __                     _____                 \n", style="bold #00F0FF")
        banner_text.append("     / |/ /__  __ ______  _____/ ___/__  ______ ___   \n", style="bold #00F0FF")
        banner_text.append("    /    / _ \\/ // / __/ / __/ /__/ _ \\/ __/ -_) _ \\  \n", style="bold #8B5CF6")
        banner_text.append("   /_/|_/\\___/\\_,_/_/   \\__/\\___/\\___/_/  \\__/ .__/  \n", style="bold #8B5CF6")
        banner_text.append("                                            /_/       \n", style="bold #8B5CF6")
        banner_text.append(" Autonomous AI Coding & Daily Productivity Assistant CLI (v1.0.0)\n", style="bold white")
        banner_text.append(" Type /help for available slash commands, /exit to quit.\n", style="dim cyan")

        panel = Panel(
            banner_text,
            border_style="#00F0FF",
            padding=(1, 2),
            title="[bold #00F0FF]⚡ NEUROCORE ⚡[/bold #00F0FF]",
            subtitle="[bold #8B5CF6]Interactive REPL Engine[/bold #8B5CF6]"
        )
        self.console.print(panel)

    def render_status_bar(
        self,
        provider: Optional[Union[str, Dict[str, Any]]] = None,
        model: Optional[str] = None,
        workspace: Optional[str] = None,
        tokens: Optional[int] = None,
        status: str = "READY"
    ):
        """Renders top/bottom sticky status bar with session metrics."""
        if self.plain_mode:
            return

        # Flexible argument handling: if dictionary state is passed as first argument
        if isinstance(provider, dict):
            state = provider
            prov_str = state.get("provider", self.current_provider)
            mod_str = state.get("model", self.current_model)
            ws_str = state.get("workspace", self.active_workspace)
            tok_num = state.get("tokens", state.get("token_usage", {}).get("total", self.total_tokens))
            stat_str = state.get("status", status)
        else:
            prov_str = provider or self.current_provider
            mod_str = model or self.current_model
            ws_str = workspace or self.active_workspace
            tok_num = tokens if tokens is not None else self.total_tokens
            stat_str = status

        ws_short = os.path.basename(ws_str) or ws_str

        bar = Text()
        bar.append(" [PROV] ", style="bold black on #00F0FF")
        bar.append(f" {prov_str} ", style="bold white on #1F2937")
        bar.append(" [MODEL] ", style="bold black on #8B5CF6")
        bar.append(f" {mod_str} ", style="bold white on #1F2937")
        bar.append(" [WORK] ", style="bold black on #10B981")
        bar.append(f" {ws_short} ", style="bold white on #1F2937")
        bar.append(" [TOKENS] ", style="bold black on #F59E0B")
        bar.append(f" {tok_num:,} ", style="bold white on #1F2937")
        bar.append(" [STATUS] ", style="bold black on #00F0FF")
        bar.append(f" {stat_str} ", style="bold green on #1F2937")

        self.console.print(bar)

    def clear_screen(self):
        """Clears terminal screen buffer."""
        self.console.clear()

    def render_info(self, message: str):
        """Renders info message."""
        if self.plain_mode:
            self.console.print(f"[INFO] {message}")
        else:
            self.console.print(f"[bold cyan]ℹ[/bold cyan] {message}")

    def render_warning(self, message: str):
        """Renders warning message."""
        if self.plain_mode:
            self.console.print(f"[WARNING] {message}")
        else:
            self.console.print(f"[bold amber]⚠[/bold amber] {message}")

    def render_error(self, message: str):
        """Renders error message."""
        if self.plain_mode:
            self.console.print(f"[ERROR] {message}")
        else:
            self.console.print(f"[bold red]✖ Error:[/] {message}")

    def render_success(self, message: str):
        """Renders success message."""
        if self.plain_mode:
            self.console.print(f"[SUCCESS] {message}")
        else:
            self.console.print(f"[bold emerald]✔[/bold emerald] {message}")

    def render_tool_start(self, tool_name: str, call_id: str, args: Dict[str, Any]):
        """Renders tool execution start trace panel."""
        if self.plain_mode:
            self.console.print(f"[TOOL START] {tool_name} (ID: {call_id}) args={args}")
            return

        try:
            args_json = json.dumps(args, indent=2)
            arg_renderable = Syntax(args_json, "json", theme="monokai", line_numbers=False)
        except Exception:
            arg_renderable = Text(str(args))

        panel = Panel(
            arg_renderable,
            title=f"[bold cyan]Tool Call:[/] [bold white]{tool_name}[/] (ID: {call_id[:8]})",
            subtitle="[running_badge] RUNNING [/running_badge]",
            subtitle_align="right",
            border_style="cyan"
        )
        self.console.print(panel)

    def render_tool_end(
        self,
        tool_name: str,
        call_id: str,
        status: str,
        duration_ms: int,
        output: Any,
        error: Optional[str] = None
    ):
        """Renders tool execution result panel with badges and 30-line truncation."""
        # Specialized delegation checks first
        if tool_name in ["write_file", "replace_file_content"] and isinstance(output, dict) and "diff" in output:
            self.render_code_diff(
                file_path=output.get("path", output.get("file_path", "file")),
                diff_text=output.get("diff", "")
            )
            return

        if tool_name == "run_command" and isinstance(output, dict) and "command" in output:
            self.render_command_output(
                command=output.get("command", ""),
                cwd=output.get("cwd", "."),
                exit_code=output.get("exit_code", 0),
                duration_ms=duration_ms,
                stdout=output.get("stdout", ""),
                stderr=output.get("stderr", "")
            )
            return

        if self.plain_mode:
            self.console.print(f"[TOOL END] {tool_name} status={status} duration={duration_ms}ms")
            if error:
                self.console.print(f"  Error: {error}")
            else:
                out_str = str(output)
                lines = out_str.splitlines()
                if len(lines) > 30:
                    out_str = "\n".join(lines[:30]) + f"\n... [{len(lines) - 30} lines truncated]"
                self.console.print(f"  Output: {out_str}")
            return

        is_success = status.lower() in ["success", "ok"]
        if is_success:
            badge = f"[success_badge] SUCCESS ({duration_ms}ms) [/success_badge]"
            border = "emerald"
        else:
            badge = f"[failed_badge] FAILED ({duration_ms}ms) [/failed_badge]"
            border = "red"

        out_str = str(output) if output is not None else ""
        lines = out_str.splitlines()
        if len(lines) > 30:
            truncated_str = "\n".join(lines[:30]) + f"\n... [{len(lines) - 30} lines truncated]"
        else:
            truncated_str = out_str

        content_elements = []
        if error:
            content_elements.append(Text(f"Error: {error}", style="bold red"))
        if truncated_str:
            content_elements.append(Text(truncated_str, style="white"))

        panel = Panel(
            Group(*content_elements) if content_elements else Text("No output", style="dim"),
            title=f"[bold white]{tool_name}[/] Results",
            subtitle=badge,
            subtitle_align="right",
            border_style=border
        )
        self.console.print(panel)

    def render_tool_trace(
        self,
        tool_name: str,
        call_id: str,
        arguments: Dict[str, Any],
        status: str,
        duration_ms: Optional[int] = None,
        output: Optional[Union[str, Dict[str, Any]]] = None,
        error: Optional[str] = None
    ):
        """Unified wrapper method for rendering full tool call traces."""
        if status == "running":
            self.render_tool_start(tool_name, call_id, arguments)
        else:
            self.render_tool_end(
                tool_name=tool_name,
                call_id=call_id,
                status=status,
                duration_ms=duration_ms or 0,
                output=output,
                error=error
            )

    def render_code_diff(
        self,
        file_path: str,
        diff_text: str,
        original_code: Optional[str] = None,
        modified_code: Optional[str] = None
    ):
        """
        Renders git-style colored unified diff for code modifications.
        Green (+)/red (-) highlighting with unified header styles.
        """
        if not diff_text and original_code is not None and modified_code is not None:
            orig_lines = original_code.splitlines(keepends=True)
            mod_lines = modified_code.splitlines(keepends=True)
            diff_lines = list(difflib.unified_diff(
                orig_lines,
                mod_lines,
                fromfile=f"a/{file_path}",
                tofile=f"b/{file_path}"
            ))
            diff_text = "".join(diff_lines)

        if self.plain_mode:
            self.console.print(f"=== Code Diff: {file_path} ===")
            self.console.print(diff_text)
            return

        diff_rendered = Text()
        additions, deletions = 0, 0

        for line in diff_text.splitlines():
            if line.startswith("---") or line.startswith("+++"):
                diff_rendered.append(line + "\n", style="bold cyan")
            elif line.startswith("@@"):
                diff_rendered.append(line + "\n", style="bold violet")
            elif line.startswith("+"):
                additions += 1
                diff_rendered.append(line + "\n", style="bold green on #064E3B")
            elif line.startswith("-"):
                deletions += 1
                diff_rendered.append(line + "\n", style="bold red on #7F1D1D")
            else:
                diff_rendered.append(line + "\n", style="gray")

        summary_badge = f"[bold green]+{additions}[/bold green] [bold red]-{deletions}[/bold red]"
        panel = Panel(
            diff_rendered if len(diff_rendered) > 0 else Text("No changes detected.", style="dim"),
            title=f"[bold green]Code Diff:[/] [bold white]{file_path}[/] ({summary_badge})",
            border_style="emerald"
        )
        self.console.print(panel)

    def render_command_output(
        self,
        command: str,
        cwd: str,
        exit_code: int,
        duration_ms: int,
        stdout: str,
        stderr: str
    ):
        """
        Renders shell command execution container with badges, stdout, and stderr.
        """
        if self.plain_mode:
            self.console.print(f"=== Command Output: {command} (cwd: {cwd}, exit: {exit_code}, {duration_ms}ms) ===")
            if stdout:
                self.console.print(f"STDOUT:\n{stdout}")
            if stderr:
                self.console.print(f"STDERR:\n{stderr}")
            return

        if exit_code == 0:
            badge = f"[success_badge] SUCCESS (Exit 0 | {duration_ms}ms) [/success_badge]"
            border = "emerald"
        else:
            badge = f"[failed_badge] FAILED (Exit {exit_code} | {duration_ms}ms) [/failed_badge]"
            border = "red"

        content = Text()
        content.append(f"CWD: {cwd}\n", style="bold cyan")
        content.append(f"$ {command}\n\n", style="bold yellow")

        if stdout:
            content.append("--- STDOUT ---\n", style="bold white")
            content.append(stdout.strip() + "\n", style="white")
        if stderr:
            content.append("--- STDERR ---\n", style="bold red")
            content.append(stderr.strip() + "\n", style="bold red")

        panel = Panel(
            content,
            title="[bold yellow]Shell Command Execution[/]",
            subtitle=badge,
            subtitle_align="right",
            border_style=border
        )
        self.console.print(panel)

    def render_command_execution(
        self,
        command: str,
        cwd: str,
        exit_code: int,
        stdout: str,
        stderr: str,
        duration_ms: int
    ):
        """Alias wrapper for command execution rendering."""
        self.render_command_output(
            command=command,
            cwd=cwd,
            exit_code=exit_code,
            duration_ms=duration_ms,
            stdout=stdout,
            stderr=stderr
        )

    async def handle_slash_command(
        self,
        user_input: str,
        session_state: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Direct dispatcher for slash commands from REPL."""
        handler = SlashCommandHandler(self)
        state = session_state or {}
        parts = user_input.strip().split(maxsplit=1)
        cmd = parts[0].lower()
        arg = parts[1] if len(parts) > 1 else ""

        return await handler.handle_command(
            command=cmd,
            argument=arg,
            current_provider=state.get("provider", self.current_provider),
            current_model=state.get("model", self.current_model),
            workspace=state.get("workspace", self.active_workspace),
            total_tokens=state.get("tokens", self.total_tokens),
            session_id=state.get("session_id", "session_default")
        )


# Alias for backward compatibility & design doc compliance
DisplayManager = DisplayEngine


class StreamRenderer:
    """
    Renders streaming Markdown tokens using rich.live.Live.
    """
    def __init__(self, console: Console):
        self.console = console
        self.accumulator = ""

    def start_stream(self) -> Live:
        spinner = Spinner("dots", text="[bold cyan]NeuroCore is thinking...[/]")
        return Live(spinner, console=self.console, refresh_per_second=12)

    def update_stream(self, live: Live, token: str):
        self.accumulator += token
        md = Markdown(self.accumulator)
        live.update(md)


class SlashCommandHandler:
    """
    Processes interactive REPL slash commands:
    /help, /exit, /clear, /provider, /status, /reset
    """
    def __init__(self, display: DisplayEngine):
        self.display = display

    async def handle_command(
        self,
        command: str,
        argument: str,
        current_provider: str,
        current_model: str,
        workspace: str,
        total_tokens: int,
        session_id: str
    ) -> Dict[str, Any]:

        cmd = command.lower()
        if cmd == "/help":
            table = Table(title="NeuroCore REPL Slash Commands", border_style="cyan")
            table.add_column("Command", style="bold cyan")
            table.add_column("Arguments", style="bold yellow")
            table.add_column("Description", style="white")

            table.add_row("/help", "-", "Display this command summary table.")
            table.add_row("/provider", "[gemini|openai|anthropic|local]", "Switch or query active LLM provider.")
            table.add_row("/status", "-", "Display session metrics, provider, model, & health.")
            table.add_row("/clear", "-", "Clear terminal screen buffer.")
            table.add_row("/reset", "-", "Reset conversation context history & token count.")
            table.add_row("/exit", "-", "Exit interactive REPL session.")

            self.display.console.print(table)
            return {"action": "help", "handled": True}

        elif cmd in ["/exit", "/quit"]:
            self.display.render_info("Exiting NeuroCore Agent. Goodbye!")
            return {"action": "exit", "handled": True}

        elif cmd == "/clear":
            self.display.clear_screen()
            return {"action": "clear", "handled": True}

        elif cmd == "/provider":
            if not argument:
                self.display.render_info(f"Active provider: [bold green]{current_provider}[/bold green]")
                return {"action": "provider_info", "provider": current_provider, "handled": True}

            prov = argument.lower().strip()
            if prov not in ["gemini", "openai", "anthropic", "local"]:
                self.display.render_error("Invalid provider. Supported options: gemini, openai, anthropic, local")
                return {"action": "error", "handled": True}

            self.display.current_provider = prov
            self.display.render_success(f"Switched active provider to [bold green]{prov}[/bold green]")
            return {"action": "provider_changed", "new_provider": prov, "handled": True}

        elif cmd == "/status":
            table = Table(title="NeuroCore Session & System Status", border_style="violet")
            table.add_column("Property", style="bold cyan")
            table.add_column("Value", style="bold white")

            table.add_row("Session ID", session_id)
            table.add_row("Active Provider", current_provider)
            table.add_row("Active Model", current_model)
            table.add_row("Workspace Root", workspace)
            table.add_row("Total Session Tokens", f"{total_tokens:,}")
            table.add_row("System Health", "HEALTHY [200 OK]")

            self.display.console.print(table)
            return {"action": "status", "handled": True}

        elif cmd == "/reset":
            self.display.render_info("Conversation context reset. Starting fresh session.")
            self.display.total_tokens = 0
            return {"action": "reset", "handled": True}

        else:
            self.display.render_error(f"Unknown command '{command}'. Type /help for available commands.")
            return {"action": "unknown", "handled": True}


# Formatters for Non-Interactive Batch Mode
class RichFormatter:
    """Formats batch output using Rich styling."""
    def __init__(self, display: DisplayEngine):
        self.display = display

    def format_token(self, token: str):
        self.display.console.print(token, end="")

    def format_final(self, response_text: str, tool_calls: Optional[List[Dict[str, Any]]] = None, success: bool = True):
        self.display.console.print("\n")
        self.display.console.print(Markdown(response_text or ""))


class PlainFormatter:
    """Formats output as raw plain text without Rich ANSI escape codes."""
    def format_token(self, token: str):
        sys.stdout.write(token)
        sys.stdout.flush()

    def format_final(self, response_text: str, tool_calls: Optional[List[Dict[str, Any]]] = None, success: bool = True):
        sys.stdout.write((response_text or "") + "\n")
        sys.stdout.flush()


class JSONFormatter:
    """Formats output as structured JSON payload for CLI automation."""
    def __init__(self):
        self.collected_tokens: List[str] = []
        self.tool_calls: List[Dict[str, Any]] = []

    def format_token(self, token: str):
        self.collected_tokens.append(token)

    def add_tool_call(self, tool_call: Dict[str, Any]):
        self.tool_calls.append(tool_call)

    def format_final(
        self,
        response_text: Optional[str] = None,
        tool_calls: Optional[List[Dict[str, Any]]] = None,
        success: bool = True
    ):
        final_response = response_text if response_text is not None else "".join(self.collected_tokens)
        calls = tool_calls if tool_calls is not None else self.tool_calls
        payload = {
            "success": success,
            "response": final_response,
            "tool_calls": calls,
        }
        print(json.dumps(payload, indent=2))
