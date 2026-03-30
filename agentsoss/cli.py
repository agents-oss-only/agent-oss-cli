"""AgentsOSS CLI entry point.

Usage:
  agentsoss           — start a contribution session (auto-setup if needed)
  agentsoss --time 30 — run for 30 minutes
  agentsoss setup     — re-run the setup wizard
  agentsoss config    — show current configuration (secrets redacted)
"""

from __future__ import annotations

import sys

import click
from rich.console import Console
from rich.table import Table

from agentsoss import __version__

console = Console()


# ---------------------------------------------------------------------------
# Main command  (runs a session when called without a subcommand)
# ---------------------------------------------------------------------------

@click.group(invoke_without_command=True)
@click.version_option(__version__, prog_name="agentsoss")
@click.option(
    "--time", "session_minutes",
    type=int,
    default=None,
    metavar="MINUTES",
    help="How long to run (minutes). Defaults to the value saved in config (60m).",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Print the prompt that would be sent without starting a session.",
)
@click.pass_context
def main(ctx: click.Context, session_minutes: int | None, dry_run: bool) -> None:
    """AgentsOSS — autonomous Claude Code agent for open source contributions.

    Run without arguments to start contributing immediately.
    """
    if ctx.invoked_subcommand is not None:
        # A subcommand was given — let it handle things
        return

    from agentsoss.config import config_exists, load_config
    from agentsoss.agent.runner import run_contribution_session

    # Auto-setup if no config exists
    if not config_exists():
        console.print(
            "[yellow]No configuration found.[/yellow] "
            "Running setup wizard...\n"
        )
        from agentsoss.setup_wizard import run_setup
        run_setup()
        console.print()

    try:
        config = load_config()
    except FileNotFoundError as e:
        console.print(f"[red]{e}[/red]")
        sys.exit(1)

    if session_minutes is not None:
        config.session_budget_minutes = session_minutes

    try:
        run_contribution_session(config, dry_run=dry_run)
    except KeyboardInterrupt:
        console.print("\n[yellow]Session stopped.[/yellow]")
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise


# ---------------------------------------------------------------------------
# setup
# ---------------------------------------------------------------------------

@main.command()
@click.option("--reconfigure", is_flag=True, help="Overwrite existing configuration.")
def setup(reconfigure: bool) -> None:
    """Run the one-time setup wizard."""
    from agentsoss.setup_wizard import run_setup
    run_setup(reconfigure=reconfigure)


# ---------------------------------------------------------------------------
# config
# ---------------------------------------------------------------------------

@main.command(name="config")
def show_config() -> None:
    """Display current configuration (token redacted)."""
    from agentsoss.config import load_config, redacted_config

    try:
        cfg = load_config()
    except FileNotFoundError as e:
        console.print(f"[red]{e}[/red]")
        sys.exit(1)

    data = redacted_config(cfg)

    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column("Key", style="bold cyan")
    table.add_column("Value")

    for key, value in data.items():
        if isinstance(value, list):
            value = ", ".join(value) if value else "[dim](none)[/dim]"
        table.add_row(key, str(value))

    console.print(table)


if __name__ == "__main__":
    main()
