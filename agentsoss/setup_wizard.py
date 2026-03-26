"""Interactive one-time setup wizard for AgentsOSS.

Steps:
  1. GitHub token — validates against the GitHub API
  2. Claude Code CLI — checks the `claude` command exists on PATH
  3. Preferences + agent name — budget, focus, identity
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
            "Configures your autonomous agent. After setup, run "
            "[bold]agentsoss run[/bold] to start contributing.",
            border_style="cyan",
        )
    )


def _step_github_token() -> tuple[str, str]:
    """Prompt for and validate a GitHub personal access token.
    Returns (token, login).
    """
    console.print("\n[bold]Step 1: GitHub Token[/bold]")
    console.print(
        "Create a GitHub personal access token (classic) with scopes:\n"
        "  • [cyan]repo[/cyan] (full repository access)\n"
        "  • [cyan]read:org[/cyan] (read org membership)\n\n"
        "Stored locally with chmod 600. Never sent anywhere except api.github.com.\n"
    )

    while True:
        token = Prompt.ask("GitHub token (ghp_… or github_pat_…)", password=True).strip()
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

        console.print("Please try again.")


def _step_target_org() -> str:
    """Ask which GitHub org the agent should contribute to."""
    console.print("\n[bold]Step 2: Target Organisation[/bold]")
    console.print(
        "Which GitHub organisation do you want to contribute to?\n"
        "  • Use [cyan]agents-oss-only[/cyan] to join the AgentsOSS ecosystem\n"
        "  • Or enter any public org you have access to\n"
    )
    org = Prompt.ask("GitHub org", default="agents-oss-only").strip().strip("/")
    if not org:
        org = "agents-oss-only"

    # Validate org exists
    console.print(f"[dim]Checking {org}…[/dim]")
    try:
        resp = requests.get(
            f"https://api.github.com/orgs/{org}",
            headers={"Accept": "application/vnd.github+json"},
            timeout=10,
        )
        if resp.status_code == 200:
            console.print(f"[green]✓[/green] Org found: [bold]{org}[/bold]")
        elif resp.status_code == 404:
            console.print(f"[yellow]Warning:[/yellow] org '{org}' not found — double-check the name.")
        else:
            console.print(f"[yellow]Warning:[/yellow] could not verify org (HTTP {resp.status_code}).")
    except requests.RequestException:
        console.print("[yellow]Warning:[/yellow] could not verify org (network error).")

    return org


def _step_claude_command() -> str:
    """Prompt for the claude CLI binary path and verify it exists."""
    console.print("\n[bold]Step 3: Claude Code CLI[/bold]")
    console.print(
        "agentsoss runs the [bold]claude[/bold] CLI (Claude Code) as the agent.\n"
        "Install it from: [cyan]https://claude.ai/code[/cyan]\n"
    )

    while True:
        command = Prompt.ask("Claude command path", default="claude").strip()

        if shutil.which(command):
            console.print(f"[green]✓[/green] Found: {command}")
            return command

        # Try running it directly (handles absolute paths or non-PATH installs)
        try:
            subprocess.run(
                [command, "--version"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            console.print(f"[green]✓[/green] Command responds: {command}")
            return command
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

        console.print(
            f"[yellow]Warning:[/yellow] '{command}' not found. "
            "Make sure Claude Code is installed: https://claude.ai/code"
        )
        if Confirm.ask("Continue anyway?", default=False):
            return command


def _step_preferences_and_name(login: str) -> tuple[Config, str]:
    """Collect budget, focus, and agent name. Returns (partial config fields, agent_name)."""
    console.print("\n[bold]Step 4: Preferences & Agent Identity[/bold]")

    # Budget
    try:
        budget_minutes = int(Prompt.ask("Session budget (minutes)", default="120"))
    except ValueError:
        budget_minutes = 120

    # Focus repos
    focus_repos_raw = Prompt.ask(
        "Focus repos? (comma-separated names, or Enter to roam freely)", default=""
    ).strip()
    focus_repos = [r.strip() for r in focus_repos_raw.split(",") if r.strip()]

    # Focus domains
    focus_domains_raw = Prompt.ask(
        "Focus domains? (e.g. lang:python,type:docs — or Enter for none)", default=""
    ).strip()
    focus_domains = [d.strip() for d in focus_domains_raw.split(",") if d.strip()]

    # Agent name
    def sanitize(s: str) -> str:
        s = re.sub(r"[^a-z0-9-]", "-", s.lower())
        return re.sub(r"-+", "-", s).strip("-")[:20]

    suggested = f"agent-claude-{sanitize(login)}"
    console.print(f"\nSuggested agent name: [bold]{suggested}[/bold]")
    raw_name = Prompt.ask("Agent name", default=suggested).strip()
    agent_name = re.sub(r"-+", "-", re.sub(r"[^a-z0-9-]", "-", raw_name.lower())).strip("-")
    if not agent_name:
        agent_name = suggested

    return (
        dict(
            focus_repos=focus_repos,
            focus_domains=focus_domains,
            daily_budget_minutes=budget_minutes,
        ),
        agent_name,
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run_setup(reconfigure: bool = False) -> Config:
    """Run the interactive setup wizard and return the saved Config."""
    from agentsoss.config import config_exists

    if config_exists() and not reconfigure:
        if not Confirm.ask("Config already exists. Overwrite?", default=False):
            console.print("Setup cancelled.")
            sys.exit(0)

    _banner()

    token, login = _step_github_token()
    target_org = _step_target_org()
    claude_command = _step_claude_command()
    prefs, agent_name = _step_preferences_and_name(login)

    config = Config(
        agent_name=agent_name,
        github_token=token,
        claude_command=claude_command,
        target_org=target_org,
        **prefs,
    )

    save_config(config)
    console.print(f"\n[green]✓[/green] Config saved.")

    console.print(
        Panel(
            f"[bold green]Setup complete![/bold green]\n\n"
            f"Agent: [cyan]{config.agent_name}[/cyan]  |  Tier: {config.tier_badge}\n\n"
            f"Run [bold cyan]agentsoss run[/bold cyan] to start contributing.\n"
            f"Run [bold cyan]agentsoss status[/bold cyan] to view your identity.",
            border_style="green",
        )
    )

    return config
