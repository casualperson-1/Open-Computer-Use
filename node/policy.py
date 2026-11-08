"""Node-side policy: what this machine will do, whoever asks.

This is the hard boundary. A node can restrict which commands it accepts, which
web hosts may be opened and which apps may be listed or launched, independent of
anything configured on the server. The server has its own policy layer; the node
never trusts it.

Configure with a JSON file (``OCC_NODE_POLICY=/path/policy.json``) and/or
environment variables, which win over the file::

    OCC_NODE_ALLOWED_COMMANDS=mouse_move,click,scroll,type,screenshot
    OCC_NODE_DENIED_DOMAINS=*.bank.example
    OCC_NODE_ALLOWED_APPS=firefox,org.gnome.Nautilus
    OCC_NODE_RATE_LIMIT=120
    OCC_NODE_AUDIT=true

An empty allow list means "everything of that kind is allowed". Deny lists win
over allow lists. The node control commands (``describe``, ``pause``,
``resume``, ``stop``) are always accepted so the kill switch keeps working.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

from shared.policy import (
    PolicyDenied,
    matches_any,
    split_patterns,
    url_allowed,
)

from .config import ConfigError

# Reaching these must never depend on policy: they are how a human stops a node.
ALWAYS_ALLOWED = frozenset({"describe", "pause", "resume", "stop"})

_FILE_KEYS = {
    "allowed_commands",
    "denied_commands",
    "allowed_domains",
    "denied_domains",
    "allowed_apps",
    "denied_apps",
    "rate_limit_per_minute",
    "audit",
}

_ENV_KEYS = {
    "allowed_commands": "OCC_NODE_ALLOWED_COMMANDS",
    "denied_commands": "OCC_NODE_DENIED_COMMANDS",
    "allowed_domains": "OCC_NODE_ALLOWED_DOMAINS",
    "denied_domains": "OCC_NODE_DENIED_DOMAINS",
    "allowed_apps": "OCC_NODE_ALLOWED_APPS",
    "denied_apps": "OCC_NODE_DENIED_APPS",
}


def _as_bool(raw: str, default: bool = True) -> bool:
    text = (raw or "").strip().lower()
    if not text:
        return default
    return text in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class NodePolicy:
    """A node's allow/deny rules, rate limit and audit setting."""

    allowed_commands: Tuple[str, ...] = ()
    denied_commands: Tuple[str, ...] = ()
    allowed_domains: Tuple[str, ...] = ()
    denied_domains: Tuple[str, ...] = ()
    allowed_apps: Tuple[str, ...] = ()
    denied_apps: Tuple[str, ...] = ()
    rate_limit_per_minute: int = 0
    audit: bool = True
    source: str = "defaults"

    # -- commands --------------------------------------------------------
    def command_allowed(self, name: str) -> bool:
        clean = (name or "").strip().lower()
        if not clean:
            return False
        if clean in ALWAYS_ALLOWED:
            return True
        if matches_any(clean, self.denied_commands):
            return False
        if self.allowed_commands:
            return matches_any(clean, self.allowed_commands)
        return True

    def check_command(self, name: str) -> None:
        if self.command_allowed(name):
            return
        raise PolicyDenied(
            f"The node's policy does not allow the '{name}' command.",
            code="not_allowed",
        )

    # -- hosts -----------------------------------------------------------
    def check_url(self, url: str) -> None:
        if url_allowed(url, self.allowed_domains, self.denied_domains):
            return
        raise PolicyDenied(
            f"The node's policy does not allow opening {url}.",
            code="domain_not_allowed",
        )

    # -- apps ------------------------------------------------------------
    def app_allowed(self, app_id: str, name: str = "") -> bool:
        identifier = (app_id or "").strip().lower()
        label = (name or "").strip().lower()
        for pattern in self.denied_apps:
            if matches_any(identifier, (pattern,)) or (label and matches_any(label, (pattern,))):
                return False
        if not self.allowed_apps:
            return True
        return matches_any(identifier, self.allowed_apps) or (
            bool(label) and matches_any(label, self.allowed_apps)
        )

    def filter_apps(self, apps: Iterable[Any]) -> List[Any]:
        kept = []
        for app in apps:
            identifier = getattr(app, "id", None)
            name = getattr(app, "name", None)
            if identifier is None and isinstance(app, Mapping):
                identifier = app.get("app_id") or app.get("id")
                name = app.get("name")
            if self.app_allowed(str(identifier or ""), str(name or "")):
                kept.append(app)
        return kept

    # -- reporting -------------------------------------------------------
    def describe(self) -> Dict[str, Any]:
        """Policy state for the handshake, status output and the control page."""
        return {
            "source": self.source,
            "allowed_commands": list(self.allowed_commands),
            "denied_commands": list(self.denied_commands),
            "allowed_domains": list(self.allowed_domains),
            "denied_domains": list(self.denied_domains),
            "allowed_apps": list(self.allowed_apps),
            "denied_apps": list(self.denied_apps),
            "rate_limit_per_minute": self.rate_limit_per_minute,
            "audit": self.audit,
        }

    def summary(self) -> str:
        if not any(
            (
                self.allowed_commands,
                self.denied_commands,
                self.allowed_domains,
                self.denied_domains,
                self.allowed_apps,
                self.denied_apps,
                self.rate_limit_per_minute,
            )
        ):
            return "unrestricted"
        parts = []
        if self.allowed_commands:
            parts.append(f"{len(self.allowed_commands)} allowed commands")
        elif self.denied_commands:
            parts.append(f"{len(self.denied_commands)} denied commands")
        if self.allowed_domains:
            parts.append(f"{len(self.allowed_domains)} allowed hosts")
        if self.denied_domains:
            parts.append(f"{len(self.denied_domains)} denied hosts")
        if self.allowed_apps:
            parts.append(f"{len(self.allowed_apps)} allowed apps")
        if self.denied_apps:
            parts.append(f"{len(self.denied_apps)} denied apps")
        if self.rate_limit_per_minute:
            parts.append(f"{self.rate_limit_per_minute}/min")
        return ", ".join(parts) or "unrestricted"

    # -- construction ----------------------------------------------------
    @classmethod
    def from_file(cls, path: Path) -> "NodePolicy":
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise ConfigError(f"Could not read the policy file {path}: {exc}") from exc
        try:
            data = json.loads(raw)
        except ValueError as exc:
            raise ConfigError(f"The policy file {path} is not valid JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise ConfigError(f"The policy file {path} must contain a JSON object.")
        unknown = sorted(set(data) - _FILE_KEYS)
        if unknown:
            raise ConfigError(
                f"The policy file {path} has unknown keys: {', '.join(unknown)}. "
                f"Allowed keys: {', '.join(sorted(_FILE_KEYS))}."
            )
        return cls._build(data, source=str(path))

    @classmethod
    def from_env(
        cls,
        env: Optional[Mapping[str, str]] = None,
        path: Optional[str] = None,
    ) -> "NodePolicy":
        values: Dict[str, Any] = {}
        source = "defaults"
        environ = os.environ if env is None else env

        policy_path = path or environ.get("OCC_NODE_POLICY") or ""
        if policy_path:
            base = cls.from_file(Path(policy_path).expanduser())
            values.update(
                {
                    "allowed_commands": base.allowed_commands,
                    "denied_commands": base.denied_commands,
                    "allowed_domains": base.allowed_domains,
                    "denied_domains": base.denied_domains,
                    "allowed_apps": base.allowed_apps,
                    "denied_apps": base.denied_apps,
                    "rate_limit_per_minute": base.rate_limit_per_minute,
                    "audit": base.audit,
                }
            )
            source = base.source

        overrides = False
        for key, variable in _ENV_KEYS.items():
            if variable in environ:
                values[key] = split_patterns(environ.get(variable))
                overrides = True

        if "OCC_NODE_RATE_LIMIT" in environ:
            raw = (environ.get("OCC_NODE_RATE_LIMIT") or "").strip()
            try:
                values["rate_limit_per_minute"] = max(0, int(raw or 0))
            except ValueError as exc:
                raise ConfigError("OCC_NODE_RATE_LIMIT must be a whole number.") from exc
            overrides = True

        if "OCC_NODE_AUDIT" in environ:
            values["audit"] = _as_bool(str(environ.get("OCC_NODE_AUDIT")), True)
            overrides = True

        if overrides:
            source = f"{source} + environment"
        return cls._build(values, source=source)

    @classmethod
    def _build(cls, data: Mapping[str, Any], *, source: str) -> "NodePolicy":
        limit = data.get("rate_limit_per_minute", 0)
        try:
            rate_limit = max(0, int(limit or 0))
        except (TypeError, ValueError) as exc:
            raise ConfigError("rate_limit_per_minute must be a whole number.") from exc
        audit = data.get("audit", True)
        return cls(
            allowed_commands=split_patterns(data.get("allowed_commands")),
            denied_commands=split_patterns(data.get("denied_commands")),
            allowed_domains=split_patterns(data.get("allowed_domains")),
            denied_domains=split_patterns(data.get("denied_domains")),
            allowed_apps=split_patterns(data.get("allowed_apps")),
            denied_apps=split_patterns(data.get("denied_apps")),
            rate_limit_per_minute=rate_limit,
            audit=bool(audit) if isinstance(audit, bool) else str(audit).lower() in ("1", "true", "yes", "on"),
            source=source,
        )


__all__ = ["ALWAYS_ALLOWED", "NodePolicy"]
