"""Server-side policy for commands sent to a desktop node.

The node enforces its own, stricter policy; this layer exists so the server can
refuse an action before it ever reaches a machine, ask the owner first, and keep
a single place where limits are configured.

Modes, from ``NODE_APPROVAL_MODE``:

``off``
    Never ask. The default, so existing behaviour is unchanged.
``risky``
    Ask for the commands in ``NODE_APPROVAL_COMMANDS`` (default: launching
    apps, opening URLs, browser actions and key combos).
``all``
    Ask before every command.

When a run already has ``APPROVAL_MODE=confirm`` the agent asks about each tool
call, so the run tells the remote computer to skip this extra prompt.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, Sequence

from shared.policy import (
    RateLimiter,
    matches_any,
    split_patterns,
    url_allowed,
)

DEFAULT_RISKY_COMMANDS = ("open_item", "open_url", "browser_action", "key_combo")
APPROVAL_MODES = ("off", "risky", "all")


@dataclass(frozen=True)
class Decision:
    """What to do with one command: ``allow``, ``deny`` or ``confirm``."""

    outcome: str
    reason: str = ""

    @property
    def allowed(self) -> bool:
        return self.outcome == "allow"


class ServerNodePolicy:
    """Allowlists, approvals and a per-run rate limit for node commands."""

    def __init__(
        self,
        *,
        allowed_commands: Sequence[str] = (),
        denied_commands: Sequence[str] = (),
        allowed_domains: Sequence[str] = (),
        denied_domains: Sequence[str] = (),
        allowed_apps: Sequence[str] = (),
        denied_apps: Sequence[str] = (),
        approval_mode: str = "off",
        approval_commands: Sequence[str] = DEFAULT_RISKY_COMMANDS,
        rate_limit_per_minute: int = 0,
    ) -> None:
        self.allowed_commands = split_patterns(allowed_commands)
        self.denied_commands = split_patterns(denied_commands)
        self.allowed_domains = split_patterns(allowed_domains)
        self.denied_domains = split_patterns(denied_domains)
        self.allowed_apps = split_patterns(allowed_apps)
        self.denied_apps = split_patterns(denied_apps)
        mode = str(approval_mode or "off").strip().lower()
        self.approval_mode = mode if mode in APPROVAL_MODES else "off"
        self.approval_commands = split_patterns(approval_commands) or DEFAULT_RISKY_COMMANDS
        try:
            self.rate_limit_per_minute = max(0, int(rate_limit_per_minute or 0))
        except (TypeError, ValueError):
            self.rate_limit_per_minute = 0

    # -- construction ----------------------------------------------------
    @classmethod
    def from_settings(cls, settings: Any) -> "ServerNodePolicy":
        return cls(
            allowed_commands=getattr(settings, "NODE_ALLOWED_COMMANDS", ""),
            denied_commands=getattr(settings, "NODE_DENIED_COMMANDS", ""),
            allowed_domains=getattr(settings, "NODE_ALLOWED_DOMAINS", ""),
            denied_domains=getattr(settings, "NODE_DENIED_DOMAINS", ""),
            allowed_apps=getattr(settings, "NODE_ALLOWED_APPS", ""),
            denied_apps=getattr(settings, "NODE_DENIED_APPS", ""),
            approval_mode=getattr(settings, "NODE_APPROVAL_MODE", "off"),
            approval_commands=getattr(settings, "NODE_APPROVAL_COMMANDS", DEFAULT_RISKY_COMMANDS),
            rate_limit_per_minute=getattr(settings, "NODE_RATE_LIMIT", 0),
        )

    def limiter(self) -> RateLimiter:
        """A fresh bucket; one per run so a busy run cannot starve another."""
        return RateLimiter(self.rate_limit_per_minute)

    # -- decisions -------------------------------------------------------
    def decide(self, name: str, params: Optional[Dict[str, Any]] = None) -> Decision:
        params = params or {}
        command = (name or "").strip().lower()

        if matches_any(command, self.denied_commands):
            return Decision("deny", f"The server policy denies the '{command}' command.")
        if self.allowed_commands and not matches_any(command, self.allowed_commands):
            return Decision("deny", f"The server policy does not allow the '{command}' command.")

        if command == "open_url":
            url = str(params.get("url") or "")
            if not url_allowed(url, self.allowed_domains, self.denied_domains):
                return Decision("deny", f"The server policy does not allow opening {url}.")
        elif command == "browser_action" and params.get("action") == "navigate":
            url = str(params.get("url") or "")
            if not url_allowed(url, self.allowed_domains, self.denied_domains):
                return Decision("deny", f"The server policy does not allow opening {url}.")

        if command == "open_item":
            app_id = str(params.get("app_id") or "").strip().lower()
            if app_id and matches_any(app_id, self.denied_apps):
                return Decision("deny", f"The server policy does not allow launching '{app_id}'.")
            if app_id and self.allowed_apps and not matches_any(app_id, self.allowed_apps):
                return Decision("deny", f"The server policy does not allow launching '{app_id}'.")

        if self.approval_mode == "all" or (
            self.approval_mode == "risky" and matches_any(command, self.approval_commands)
        ):
            return Decision(
                "confirm",
                f"'{command}' needs your approval before it runs on the node.",
            )
        return Decision("allow")

    # -- reporting -------------------------------------------------------
    def describe(self) -> Dict[str, Any]:
        return {
            "allowed_commands": list(self.allowed_commands),
            "denied_commands": list(self.denied_commands),
            "allowed_domains": list(self.allowed_domains),
            "denied_domains": list(self.denied_domains),
            "allowed_apps": list(self.allowed_apps),
            "denied_apps": list(self.denied_apps),
            "approval_mode": self.approval_mode,
            "approval_commands": list(self.approval_commands),
            "rate_limit_per_minute": self.rate_limit_per_minute,
        }

    def summary(self) -> str:
        parts = []
        if self.allowed_commands:
            parts.append(f"{len(self.allowed_commands)} allowed commands")
        if self.denied_commands:
            parts.append(f"{len(self.denied_commands)} denied commands")
        if self.allowed_domains:
            parts.append(f"{len(self.allowed_domains)} allowed hosts")
        if self.denied_domains:
            parts.append(f"{len(self.denied_domains)} denied hosts")
        if self.allowed_apps:
            parts.append(f"{len(self.allowed_apps)} allowed apps")
        if self.allowed_apps or self.allowed_domains or self.allowed_commands:
            pass
        if self.approval_mode != "off":
            parts.append(f"approval: {self.approval_mode}")
        if self.rate_limit_per_minute:
            parts.append(f"{self.rate_limit_per_minute}/min")
        return ", ".join(parts) or "unrestricted"


__all__ = [
    "APPROVAL_MODES",
    "DEFAULT_RISKY_COMMANDS",
    "Decision",
    "ServerNodePolicy",
]
