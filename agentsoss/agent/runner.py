"""Budget-aware session runner.

Runs repeated Claude Code tasks within the configured time budget.
Each task is a fresh `claude -p "..."` invocation; the agent autonomously
decides what to work on. The loop continues until the budget is exhausted
or the user interrupts with Ctrl+C.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from typing import TYPE_CHECKING

from rich.console import Console
from rich.panel import Panel
from rich.rule import Rule
from rich.text import Text

from .prompt import build_system_prompt

if TYPE_CHECKING:
    from agentsoss.config import Config

# Minimum remaining budget (seconds) to start another task.
_MIN_TASK_SECONDS = 90
# Pause between tasks so the user can read output and interrupt.
_INTER_TASK_PAUSE = 3

# Tool names → human-readable action labels
_TOOL_LABELS: dict[str, str] = {
    "Bash": "Running shell command",
    "Read": "Reading file",
    "Write": "Writing file",
    "Edit": "Editing file",
    "Grep": "Searching code",
    "Glob": "Finding files",
    "WebSearch": "Searching the web",
    "WebFetch": "Fetching URL",
    "AskUserQuestion": "Asking a question",
    "TodoWrite": "Updating task list",
    "Task": "Spawning sub-agent",
}


def run_contribution_session(config: "Config", dry_run: bool = False) -> None:
    """Loop through tasks until the budget is exhausted or the user interrupts."""
    console = Console()
    budget_seconds = config.session_budget_minutes * 60
    start_time = time.monotonic()
    task_count = 0

    _print_session_header(console, config)

    if dry_run:
        remaining = config.session_budget_minutes
        prompt = build_system_prompt(config, remaining_minutes=float(remaining))
        console.print(
            Panel(
                f"[yellow]DRY RUN[/yellow] — command that would run:\n\n"
                f"[dim]{config.claude_command} -p [PROMPT] "
                f"--dangerously-skip-permissions --output-format stream-json[/dim]\n\n"
                f"[dim]Prompt ({len(prompt)} chars):[/dim]\n{prompt[:800]}…",
                border_style="yellow",
            )
        )
        return

    try:
        while True:
            elapsed = time.monotonic() - start_time
            remaining_seconds = budget_seconds - elapsed

            if remaining_seconds < _MIN_TASK_SECONDS:
                console.print(
                    f"\n[dim]Less than {_MIN_TASK_SECONDS}s remaining — wrapping up.[/dim]"
                )
                break

            task_count += 1
            remaining_minutes = remaining_seconds / 60

            console.print(
                Rule(
                    Text(
                        f"  Task {task_count}  ·  {remaining_minutes:.0f}m remaining  ",
                        style="bold dim",
                    ),
                    style="dim",
                )
            )

            prompt = build_system_prompt(config, remaining_minutes=remaining_minutes)
            _run_single_task(config, prompt, console)

            elapsed = time.monotonic() - start_time
            if budget_seconds - elapsed < _MIN_TASK_SECONDS:
                break

            console.print(
                f"\n[dim]Pausing {_INTER_TASK_PAUSE}s before next task — "
                f"[bold]Ctrl+C[/bold] to stop.[/dim]"
            )
            time.sleep(_INTER_TASK_PAUSE)

    except KeyboardInterrupt:
        console.print("\n[yellow]Session stopped.[/yellow]")

    total_elapsed = (time.monotonic() - start_time) / 60
    console.print(
        Panel(
            f"[bold]Tasks:[/bold] {task_count}  ·  "
            f"[bold]Time:[/bold] {total_elapsed:.1f}m / {config.session_budget_minutes}m",
            title="[bold]Session Complete[/bold]",
            border_style="cyan",
        )
    )


def _print_session_header(console: Console, config: "Config") -> None:
    lines = [
        f"[bold cyan]AgentsOSS[/bold cyan]  ·  autonomous open source agent",
        "",
        f"  Agent  [cyan]{config.agent_name}[/cyan]",
        f"  Org    [cyan]{config.target_org}[/cyan]",
        f"  Budget [cyan]{config.session_budget_minutes}m[/cyan]",
    ]
    if config.focus_repos:
        lines.append(f"  Focus  [cyan]{', '.join(config.focus_repos)}[/cyan]")
    lines += [
        "",
        "[dim]Press Ctrl+C at any time to stop cleanly.[/dim]",
    ]
    console.print(Panel("\n".join(lines), border_style="cyan", padding=(0, 1)))


def _run_single_task(config: "Config", prompt: str, console: Console) -> None:
    """Invoke claude for a single task and stream its output."""
    cmd = [
        config.claude_command,
        "-p", prompt,
        "--dangerously-skip-permissions",
        "--output-format", "stream-json",
        "--verbose",
    ]

    env = os.environ.copy()
    env["GH_TOKEN"] = config.github_token
    env["GITHUB_TOKEN"] = config.github_token

    proc = subprocess.Popen(
        cmd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    state = _StreamState()
    try:
        for line in proc.stdout:  # type: ignore[union-attr]
            _handle_stream_event(line.rstrip(), console, state)
    finally:
        proc.wait()


class _StreamState:
    """Tracks state across stream events for clean output formatting."""
    def __init__(self) -> None:
        self.last_was_text = False
        self.text_buffer = ""


def _handle_stream_event(line: str, console: Console, state: _StreamState) -> None:
    """Parse a stream-json event and display it in a clean, informative way."""
    if not line:
        return

    try:
        event = json.loads(line)
    except json.JSONDecodeError:
        # Raw output (stderr, tool stdout, etc.) — print as-is but dim
        console.print(f"[dim]{line}[/dim]", markup=True)
        return

    etype = event.get("type")

    # ── Assistant message (text + tool calls) ────────────────────────────────
    if etype == "assistant":
        content = event.get("message", {}).get("content", [])
        for block in content:
            btype = block.get("type")

            if btype == "text":
                text = block.get("text", "")
                if text:
                    # Flush any pending newline from previous tool line
                    if not state.last_was_text:
                        console.print()
                    console.print(text, end="", markup=False)
                    state.last_was_text = True

            elif btype == "tool_use":
                tool_name = block.get("name", "tool")
                tool_input = block.get("input", {})
                label = _TOOL_LABELS.get(tool_name, tool_name)
                detail = _format_tool_detail(tool_name, tool_input)

                # Newline after streamed text
                if state.last_was_text:
                    console.print()
                    state.last_was_text = False

                console.print(
                    f"[dim]  → {label}{detail}[/dim]"
                )

    # ── Tool result ───────────────────────────────────────────────────────────
    elif etype == "tool_result":
        # Tool results are verbose; skip them to keep output clean
        pass

    # ── Final result ─────────────────────────────────────────────────────────
    elif etype == "result":
        if state.last_was_text:
            console.print()
            state.last_was_text = False

        if event.get("is_error"):
            console.print(
                Panel(
                    str(event.get("result", "Unknown error")),
                    title="[bold red]Task Error[/bold red]",
                    border_style="red",
                )
            )
        else:
            cost = event.get("cost_usd") or 0
            turns = event.get("num_turns") or 0
            console.print(
                Panel(
                    f"[green]✓[/green]  Turns: [bold]{turns}[/bold]  ·  "
                    f"Cost: [bold]${cost:.4f}[/bold]",
                    title="[bold green]Task Complete[/bold green]",
                    border_style="green",
                )
            )

    # etype == "system" → silently ignore


def _format_tool_detail(tool_name: str, tool_input: dict) -> str:
    """Return a short, human-readable description of what a tool is doing."""
    try:
        if tool_name == "Bash":
            cmd = tool_input.get("command", "")
            # Show only first meaningful line, truncated
            first_line = cmd.strip().splitlines()[0] if cmd.strip() else ""
            return f": [italic]{first_line[:80]}[/italic]" if first_line else ""

        if tool_name in ("Read", "Write", "Edit"):
            path = tool_input.get("file_path", tool_input.get("path", ""))
            if path:
                # Show just filename + parent dir to keep it short
                from pathlib import Path
                p = Path(path)
                short = f"{p.parent.name}/{p.name}" if p.parent.name else p.name
                return f": [italic]{short}[/italic]"

        if tool_name == "Grep":
            pattern = tool_input.get("pattern", "")
            return f": [italic]{pattern[:40]}[/italic]" if pattern else ""

        if tool_name == "WebSearch":
            query = tool_input.get("query", "")
            return f": [italic]{query[:60]}[/italic]" if query else ""

        if tool_name == "WebFetch":
            url = tool_input.get("url", "")
            # Show domain only
            import urllib.parse
            host = urllib.parse.urlparse(url).netloc
            return f": [italic]{host}[/italic]" if host else ""

    except Exception:
        pass

    return ""
