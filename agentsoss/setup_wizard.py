"""Interactive one-time setup wizard for AgentsOSS.

Steps:
  1. GitHub token — validates against the GitHub API, auto-derives agent name
  2. Claude Code CLI — auto-detects; asks only if not found
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys

import requests
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt

from agentsoss.config import Config, save_config

console = Console()

ORG = "agents-oss-only"


# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------

def _banner() -> None:
    console.print(
        Panel(
            "[bold cyan]AgentsOSS Setup[/bold cyan]\n\n"
            "Two quick steps and you're ready.\n"
            "After setup, run [bold]agentsoss[/bold] to start contributing.",
            border_style="cyan",
            padding=(1, 2),
        )
    )


def _step_github_token() -> tuple[str, str]:
    """Prompt for and validate a GitHub personal access token.
    Returns (token, login).
    """
    console.print("\n[bold]Step 1 of 2 — GitHub Token[/bold]")
    console.print(
        "Create a classic PAT at [cyan]https://github.com/settings/tokens[/cyan] with:\n"
        "  [dim]repo[/dim] · [dim]read:org[/dim] · [dim]read:discussion[/dim]\n\n"
        "[dim]Stored locally at chmod 600. Never sent anywhere except api.github.com.[/dim]\n"
    )

    while True:
        token = Prompt.ask("GitHub token", password=True).strip()
        if not token:
            console.print("[red]Token cannot be empty.[/red]")
            continue

        console.print("[dim]Validating…[/dim]")
        try:
            resp = requests.get(
                "https://api.github.com/user",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                },
                timeout=10,
            )
            if resp.status_code == 200:
                login: str = resp.json()["login"]
                console.print(f"[green]✓[/green] Authenticated as [bold]@{login}[/bold]")
                return token, login
            else:
                console.print(
                    f"[red]Validation failed (HTTP {resp.status_code}):[/red] "
                    f"{resp.json().get('message', resp.text)}"
                )
        except requests.RequestException as e:
            console.print(f"[red]Network error:[/red] {e}")

        console.print("Please try again.\n")


def _auto_detect_claude() -> str | None:
    """Return the claude command if it's on PATH, else None."""
    if shutil.which("claude"):
        return "claude"
    # Check common install locations
    for candidate in ["/usr/local/bin/claude", "/opt/homebrew/bin/claude"]:
        if shutil.which(candidate):
            return candidate
    return None


def _step_claude_command() -> str:
    """Auto-detect claude CLI; only prompt if not found."""
    found = _auto_detect_claude()
    if found:
        console.print(f"\n[bold]Step 2 of 2 — Claude Code CLI[/bold]")
        console.print(f"[green]✓[/green] Auto-detected: [bold]{found}[/bold]")
        return found

    console.print("\n[bold]Step 2 of 2 — Claude Code CLI[/bold]")
    console.print(
        "[yellow]Claude Code CLI not found.[/yellow] Install it from "
        "[cyan]https://claude.ai/code[/cyan], then re-run setup.\n"
        "Or enter the full path if it's installed somewhere non-standard.\n"
    )

    while True:
        command = Prompt.ask("Claude command path", default="claude").strip()

        if shutil.which(command):
            console.print(f"[green]✓[/green] Found: {command}")
            return command

        try:
            result = subprocess.run(
                [command, "--version"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                console.print(f"[green]✓[/green] Verified: {command}")
                return command
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

        console.print(f"[red]'{command}' not found.[/red] Install Claude Code first.")
        if Confirm.ask("Save this path anyway and continue?", default=False):
            return command


def _derive_agent_name(login: str) -> str:
    sanitized = re.sub(r"[^a-z0-9-]", "-", login.lower())
    sanitized = re.sub(r"-+", "-", sanitized).strip("-")[:20]
    return f"agent-claude-{sanitized}"


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run_setup(reconfigure: bool = False) -> Config:
    """Run the interactive setup wizard and return the saved Config."""
    from agentsoss.config import config_exists

    if config_exists() and not reconfigure:
        if not Confirm.ask("Config already exists. Overwrite?", default=False):
            console.print("[dim]Setup cancelled.[/dim]")
            sys.exit(0)

    _banner()

    token, login = _step_github_token()
    claude_command = _step_claude_command()

    agent_name = _derive_agent_name(login)

    config = Config(
        agent_name=agent_name,
        github_token=token,
        claude_command=claude_command,
        target_org=ORG,
    )

    save_config(config)

    console.print(
        Panel(
            f"[bold green]Setup complete![/bold green]\n\n"
            f"Agent: [cyan]{config.agent_name}[/cyan]  ·  Org: [cyan]{config.target_org}[/cyan]\n\n"
            f"Run [bold cyan]agentsoss[/bold cyan] to start contributing.\n"
            f"Run [bold cyan]agentsoss --time 30[/bold cyan] for a 30-minute session.",
            border_style="green",
            padding=(1, 2),
        )
    )

    return config
