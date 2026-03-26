"""AgentsOSS CLI entry point.

Commands:
  agentsoss setup    — one-time interactive setup
  agentsoss run      — start an autonomous contribution session
  agentsoss status   — show agent identity and config
  agentsoss config   — display current config (secrets redacted)
"""

from __future__ import annotations

import json
import subprocess
import sys

import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from agentsoss import __version__

console = Console()


# ---------------------------------------------------------------------------
# CLI group
# ---------------------------------------------------------------------------

@click.group()
@click.version_option(__version__, prog_name="agentsoss")
def main() -> None:
    """AgentsOSS — autonomous Claude Code agent for open source contributions."""


# ---------------------------------------------------------------------------
# setup
# ---------------------------------------------------------------------------

@main.command()
@click.option("--reconfigure", is_flag=True, help="Overwrite existing configuration.")
def setup(reconfigure: bool) -> None:
    """Run the interactive one-time setup wizard."""
    from agentsoss.setup_wizard import run_setup
    run_setup(reconfigure=reconfigure)


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------

@main.command()
@click.option("--dry-run", is_flag=True, help="Build the prompt but do not start a session.")
@click.option(
    "--budget",
    type=int,
    default=None,
    metavar="MINUTES",
    help="Override session budget (minutes).",
)
def run(dry_run: bool, budget: int | None) -> None:
    """Start an autonomous contribution session."""
    from agentsoss.config import load_config
    from agentsoss.agent.runner import run_contribution_session

    try:
        config = load_config()
    except FileNotFoundError as e:
        console.print(f"[red]{e}[/red]")
        sys.exit(1)

    if budget is not None:
        config.daily_budget_minutes = budget

    if dry_run:
        console.print(
            Panel("[yellow]DRY RUN MODE[/yellow] — session will not be started.",
                  border_style="yellow")
        )

    try:
        run_contribution_session(config, dry_run=dry_run)
    except KeyboardInterrupt:
        console.print("\n[yellow]Session interrupted by user.[/yellow]")
    except Exception as e:
        console.print(f"[red]Session error:[/red] {e}")
        raise


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------

@main.command()
@click.option(
    "--sync",
    is_flag=True,
    help="Fetch latest contribution score from the registry and update local config.",
)
def status(sync: bool) -> None:
    """Show agent identity, trust tier, and contribution score."""
    from agentsoss.config import load_config, save_config

    try:
        config = load_config()
    except FileNotFoundError as e:
        console.print(f"[red]{e}[/red]")
        sys.exit(1)

    if sync:
        _sync_contribution_score(config)

    table = Table(title=f"Agent: {config.agent_name}", show_header=False, box=None)
    table.add_column("Field", style="bold cyan")
    table.add_column("Value")

    table.add_row("Name", config.agent_name)
    table.add_row("Target org", config.target_org)
    table.add_row("Claude command", config.claude_command)
    table.add_row("Trust tier", config.tier_badge)
    table.add_row("Contribution score", str(config.contribution_score))
    table.add_row("Session budget", f"{config.daily_budget_minutes} min")

    if config.focus_repos:
        table.add_row("Focus repos", ", ".join(config.focus_repos))
    if config.focus_domains:
        table.add_row("Focus domains", ", ".join(config.focus_domains))

    console.print(table)

    cs = config.contribution_score
    tier = config.trust_tier
    if tier == "newcomer":
        console.print(f"\n[dim]Next tier (contributor) at 50 CS — need {max(0, 50 - cs)} more.[/dim]")
    elif tier == "contributor":
        console.print(f"\n[dim]Next tier (trusted) at 200 CS — need {max(0, 200 - cs)} more.[/dim]")
    elif tier == "trusted":
        console.print(f"\n[dim]Next tier (maintainer) at 500 CS — need {max(0, 500 - cs)} more.[/dim]")
    else:
        console.print("\n[dim]You are a maintainer — the highest tier.[/dim]")


def _sync_contribution_score(config: "Config") -> None:  # type: ignore[name-defined]
    """Fetch latest CS from the registry via gh CLI and update local config."""
    from agentsoss.config import save_config

    console.print("[dim]Fetching contribution score from registry…[/dim]")
    try:
        result = subprocess.run(
            [
                "gh", "api",
                f"repos/{config.target_org}/registry/contents/agents/{config.agent_name}.yaml",
                "--jq", ".content",
            ],
            capture_output=True,
            text=True,
            timeout=15,
            env={**__import__("os").environ, "GH_TOKEN": config.github_token},
        )
        if result.returncode == 0:
            import base64
            import yaml as _yaml
            content = base64.b64decode(result.stdout.strip()).decode()
            data = _yaml.safe_load(content)
            new_cs = int(data.get("contribution_score", config.contribution_score))
            if new_cs != config.contribution_score:
                config.contribution_score = new_cs
                save_config(config)
                console.print(f"[green]✓[/green] CS updated: {new_cs}")
            else:
                console.print(f"[dim]CS unchanged: {new_cs}[/dim]")
        else:
            console.print(f"[yellow]Could not sync (registry entry may not exist yet).[/yellow]")
    except Exception as e:
        console.print(f"[yellow]Sync failed:[/yellow] {e}")


# ---------------------------------------------------------------------------
# config
# ---------------------------------------------------------------------------

@main.command(name="config")
def show_config() -> None:
    """Display current configuration (secrets redacted)."""
    from agentsoss.config import load_config, redacted_config

    try:
        config = load_config()
    except FileNotFoundError as e:
        console.print(f"[red]{e}[/red]")
        sys.exit(1)

    console.print_json(json.dumps(redacted_config(config), ensure_ascii=False))


if __name__ == "__main__":
    main()
