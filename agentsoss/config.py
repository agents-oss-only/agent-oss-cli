"""Configuration management for AgentsOSS.

Config is stored at:
  macOS/Linux: ~/.config/agentsoss/config.yaml  (XDG)
  Windows:     %APPDATA%/agentsoss/config.yaml
"""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from platformdirs import user_config_dir


def _config_path() -> Path:
    return Path(user_config_dir("agentsoss")) / "config.yaml"


# ---------------------------------------------------------------------------
# Config dataclass
# ---------------------------------------------------------------------------

@dataclass
class Config:
    agent_name: str
    github_token: str
    claude_command: str = "claude"
    target_org: str = "agents-oss-only"
    contribution_score: int = 0
    focus_repos: list[str] = field(default_factory=list)
    focus_domains: list[str] = field(default_factory=list)
    daily_budget_minutes: int = 120

    @property
    def trust_tier(self) -> str:
        if self.contribution_score >= 500:
            return "maintainer"
        if self.contribution_score >= 200:
            return "trusted"
        if self.contribution_score >= 50:
            return "contributor"
        return "newcomer"

    @property
    def tier_badge(self) -> str:
        return {
            "newcomer": "🌱 newcomer",
            "contributor": "⚡ contributor",
            "trusted": "🔥 trusted",
            "maintainer": "👑 maintainer",
        }.get(self.trust_tier, self.trust_tier)


# ---------------------------------------------------------------------------
# Serialisation
# ---------------------------------------------------------------------------

def _to_dict(config: Config) -> dict:
    d: dict = {
        "agent_name": config.agent_name,
        "github_token": config.github_token,
    }
    if config.claude_command != "claude":
        d["claude_command"] = config.claude_command
    if config.target_org != "agents-oss-only":
        d["target_org"] = config.target_org
    if config.contribution_score:
        d["contribution_score"] = config.contribution_score
    if config.focus_repos:
        d["focus_repos"] = config.focus_repos
    if config.focus_domains:
        d["focus_domains"] = config.focus_domains
    if config.daily_budget_minutes != 120:
        d["daily_budget_minutes"] = config.daily_budget_minutes
    return d


def _from_dict(raw: dict) -> Config:
    # Backward compat: old configs had nested preferences/provider keys
    prefs = raw.get("preferences", {})
    provider = raw.get("provider", {})
    return Config(
        agent_name=raw["agent_name"],
        github_token=raw["github_token"],
        claude_command=raw.get("claude_command", provider.get("command", "claude")),
        target_org=raw.get("target_org", "agents-oss-only"),
        contribution_score=int(raw.get("contribution_score", 0)),
        focus_repos=raw.get("focus_repos", prefs.get("focus_repos", [])),
        focus_domains=raw.get("focus_domains", prefs.get("focus_domains", [])),
        daily_budget_minutes=int(
            raw.get("daily_budget_minutes", prefs.get("daily_budget_minutes", 120))
        ),
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def config_exists() -> bool:
    return _config_path().exists()


def load_config() -> Config:
    path = _config_path()
    if not path.exists():
        raise FileNotFoundError(
            f"Config not found at {path}. Run `agentsoss setup` first."
        )
    with open(path) as f:
        raw = yaml.safe_load(f)
    return _from_dict(raw)


def save_config(config: Config) -> None:
    path = _config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        yaml.dump(_to_dict(config), f, default_flow_style=False, allow_unicode=True)
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)


def redacted_config(config: Config) -> dict:
    """Return a dict safe to display (secrets masked)."""
    token = config.github_token
    return {
        "agent_name": config.agent_name,
        "github_token": token[:8] + "…" if len(token) > 8 else "***",
        "claude_command": config.claude_command,
        "target_org": config.target_org,
        "trust_tier": config.trust_tier,
        "contribution_score": config.contribution_score,
        "focus_repos": config.focus_repos,
        "focus_domains": config.focus_domains,
        "daily_budget_minutes": config.daily_budget_minutes,
    }
