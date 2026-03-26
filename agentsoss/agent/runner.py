"""Budget-aware session runner.

Runs repeated Claude Code tasks within the configured time budget.
Each task is a fresh `claude -p "..."` invocation; the agent autonomously
picks what to work on each time. The loop continues until the budget is
exhausted or the user interrupts with Ctrl+C.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from typing import TYPE_CHECKING

from rich.console import Console
from rich.panel import Panel

from .prompt import build_system_prompt

if TYPE_CHECKING:
    from agentsoss.config import Config

# Minimum remaining budget (seconds) to bother starting another task.
_MIN_TASK_SECONDS = 90
# Pause between tasks so the user can see output and interrupt if desired.
_INTER_TASK_PAUSE = 4


def run_contribution_session(config: "Config", dry_run: bool = False) -> None:
    """Loop through tasks until the budget is exhausted or the user interrupts."""
    console = Console()
    budget_seconds = config.daily_budget_minutes * 60
    start_time = time.monotonic()
    task_count = 0

    console.print(
        Panel(
            f"[bold green]AgentsOSS Session Starting[/bold green]\n"
            f"Agent: [cyan]{config.agent_name}[/cyan]  |  "
            f"Org: [cyan]{config.target_org}[/cyan]  |  "
            f"Tier: {config.tier_badge}  |  "
            f"Budget: {config.daily_budget_minutes}m",
        )
    )

    if dry_run:
        remaining = config.daily_budget_minutes
        prompt = build_system_prompt(config, remaining_minutes=float(remaining))
        console.print("[yellow]DRY RUN — session will not be started. Command that would run:[/yellow]")
        console.print(
            f"  [dim]{config.claude_command} -p [PROMPT] "
            f"--dangerously-skip-permissions --output-format stream-json --verbose[/dim]"
        )
        console.print(f"\n[dim]Prompt preview:[/dim]\n{prompt[:1200]}…")
        return

    try:
        while True:
            elapsed = time.monotonic() - start_time
            remaining_seconds = budget_seconds - elapsed

            if remaining_seconds < _MIN_TASK_SECONDS:
                console.print(
                    f"\n[dim]Less than {_MIN_TASK_SECONDS}s remaining — stopping.[/dim]"
                )
                break

            task_count += 1
            remaining_minutes = remaining_seconds / 60
            console.rule(
                f"[dim]Task {task_count} — {remaining_minutes:.1f}m remaining[/dim]"
            )

            prompt = build_system_prompt(config, remaining_minutes=remaining_minutes)
            _run_single_task(config, prompt, console)

            # Re-check budget after task completes
            elapsed = time.monotonic() - start_time
            if budget_seconds - elapsed < _MIN_TASK_SECONDS:
                break

            # Brief pause so the user can read output and interrupt
            console.print(
                f"\n[dim]Task {task_count} complete. "
                f"Next task starts in {_INTER_TASK_PAUSE}s — Ctrl+C to stop.[/dim]"
            )
            time.sleep(_INTER_TASK_PAUSE)

    except KeyboardInterrupt:
        console.print("\n[yellow]Session interrupted by user.[/yellow]")

    total_elapsed = (time.monotonic() - start_time) / 60
    console.print(
        Panel(
            f"Tasks completed: [bold]{task_count}[/bold]  |  "
            f"Elapsed: [bold]{total_elapsed:.1f}m[/bold] / {config.daily_budget_minutes}m",
            title="[bold]Session Summary[/bold]",
            border_style="dim",
        )
    )


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

    try:
        for line in proc.stdout:  # type: ignore[union-attr]
            _handle_stream_event(line.rstrip(), console)
    finally:
        proc.wait()


def _handle_stream_event(line: str, console: Console) -> None:
    """Parse a stream-json event and display it."""
    if not line:
        return

    try:
        event = json.loads(line)
    except json.JSONDecodeError:
        # Raw output (stderr, tool stdout, etc.) — print as-is
        console.print(line, markup=False)
        return

    etype = event.get("type")

    if etype == "assistant":
        for block in event.get("message", {}).get("content", []):
            if block.get("type") == "text":
                text = block["text"]
                if text:
                    console.print(text, end="", markup=False)

    elif etype == "result":
        console.print()  # newline after streamed text
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
                    f"Turns: {turns}  |  Cost: ${cost:.4f}",
                    title="[bold green]Task Complete[/bold green]",
                    border_style="green",
                )
            )

    # etype == "system" → silently ignore
