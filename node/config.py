"""Node configuration from the environment, with CLI overrides."""

from __future__ import annotations

import os
import platform
import socket
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit, urlunsplit

DEFAULT_CONTROL_PORT = 8791
DEFAULT_HEARTBEAT_SECONDS = 15.0
DEFAULT_RECONNECT_MAX_SECONDS = 60.0
NODE_WS_PATH = "/ws/node"

_SCHEME_MAP = {"http": "ws", "https": "wss", "ws": "ws", "wss": "wss"}


class ConfigError(ValueError):
    """Raised when the node is misconfigured."""


def normalise_server_url(raw: str) -> str:
    """Turn a server address into the node WebSocket URL.

    ``https://agent.example.com``, ``wss://agent.example.com`` and
    ``agent.example.com`` all become ``wss://agent.example.com/ws/node``.
    """
    text = (raw or "").strip()
    if not text:
        raise ConfigError("A server URL is required (for example https://agent.example.com).")
    if "://" not in text:
        text = f"wss://{text}"

    parsed = urlsplit(text)
    scheme = _SCHEME_MAP.get(parsed.scheme.lower())
    if scheme is None:
        raise ConfigError(f"Unsupported server scheme '{parsed.scheme}'.")
    if not parsed.netloc:
        raise ConfigError("The server URL has no host.")
    if parsed.username or parsed.password:
        raise ConfigError("Put the node token in OCC_NODE_TOKEN, not in the URL.")
    if parsed.query or parsed.fragment:
        raise ConfigError("The server URL must not contain a query or fragment.")

    path = parsed.path.rstrip("/")
    if not path.endswith(NODE_WS_PATH):
        path = f"{path}{NODE_WS_PATH}" if path else NODE_WS_PATH
    return urlunsplit((scheme, parsed.netloc, path, "", ""))


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be a number.") from exc


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer.") from exc


def read_token(token: Optional[str], token_file: Optional[str]) -> str:
    """Token from the CLI flag, a file, or the environment."""
    if token and token.strip():
        return token.strip()
    if token_file:
        try:
            value = Path(token_file).expanduser().read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise ConfigError(f"Could not read the token file: {exc}") from exc
        if not value:
            raise ConfigError("The token file is empty.")
        return value
    value = (os.environ.get("OCC_NODE_TOKEN") or "").strip()
    if not value:
        raise ConfigError(
            "No node token. Create one in the server UI (Nodes) and pass --token, "
            "--token-file or OCC_NODE_TOKEN."
        )
    if len(value) < 16:
        raise ConfigError("The node token looks too short; create a fresh one in the server UI.")
    return value


@dataclass
class NodeConfig:
    server_url: str
    token: str
    name: str = ""
    data_dir: Path = Path("./node-data")
    backend: str = "desktop"
    control_host: str = "127.0.0.1"
    control_port: int = DEFAULT_CONTROL_PORT
    heartbeat_seconds: float = DEFAULT_HEARTBEAT_SECONDS
    reconnect_max_seconds: float = DEFAULT_RECONNECT_MAX_SECONDS
    max_width: int = 1280
    human_mouse: bool = True
    browser_profile: Optional[Path] = None
    headless: Optional[bool] = None
    allow_remote_control_page: bool = False
    policy_file: Optional[Path] = None
    indicator: bool = True

    @property
    def websocket_url(self) -> str:
        return normalise_server_url(self.server_url)

    @property
    def label(self) -> str:
        return self.name or socket.gethostname() or "node"

    @property
    def control_token_path(self) -> Path:
        return self.data_dir / "control.token"

    @property
    def pid_path(self) -> Path:
        return self.data_dir / "node.pid"

    @property
    def node_id_path(self) -> Path:
        return self.data_dir / "node.id"

    @property
    def audit_path(self) -> Path:
        return self.data_dir / "audit.jsonl"


    def ensure_dirs(self) -> Path:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.data_dir, 0o700)
        except OSError:
            pass
        return self.data_dir

    def profile_dir(self) -> Path:
        if self.browser_profile is not None:
            return self.browser_profile
        return self.data_dir / "browser_profile"

    @classmethod
    def from_env(
        cls,
        *,
        server: Optional[str] = None,
        token: Optional[str] = None,
        token_file: Optional[str] = None,
        name: Optional[str] = None,
        data_dir: Optional[str] = None,
        backend: Optional[str] = None,
        control_port: Optional[int] = None,
        heartbeat: Optional[float] = None,
        headless: Optional[bool] = None,
        policy: Optional[str] = None,
        indicator: Optional[bool] = None,
    ) -> "NodeConfig":
        server_url = server or os.environ.get("OCC_NODE_SERVER", "")
        if not server_url:
            raise ConfigError("Set OCC_NODE_SERVER or pass --server.")
        resolved_token = read_token(token, token_file)
        dir_value = data_dir or os.environ.get("OCC_NODE_DATA_DIR") or "./node-data"
        profile = os.environ.get("OCC_NODE_BROWSER_PROFILE")
        return cls(
            server_url=server_url,
            token=resolved_token,
            name=name or os.environ.get("OCC_NODE_NAME") or socket.gethostname(),
            data_dir=Path(dir_value).expanduser(),
            backend=(backend or os.environ.get("OCC_NODE_BACKEND") or "desktop").strip().lower(),
            control_port=control_port
            if control_port is not None
            else _env_int("OCC_NODE_CONTROL_PORT", DEFAULT_CONTROL_PORT),
            heartbeat_seconds=heartbeat
            if heartbeat is not None
            else _env_float("OCC_NODE_HEARTBEAT", DEFAULT_HEARTBEAT_SECONDS),
            reconnect_max_seconds=_env_float(
                "OCC_NODE_RECONNECT_MAX", DEFAULT_RECONNECT_MAX_SECONDS
            ),
            max_width=_env_int("OCC_NODE_MAX_WIDTH", 1280),
            human_mouse=_env_bool("OCC_NODE_HUMAN_MOUSE", True),
            browser_profile=Path(profile).expanduser() if profile else None,
            headless=headless,
            policy_file=Path(policy or os.environ.get("OCC_NODE_POLICY") or "").expanduser()
            if (policy or os.environ.get("OCC_NODE_POLICY"))
            else None,
            indicator=_env_bool("OCC_NODE_INDICATOR", True) if indicator is None else indicator,
        )


def session_info() -> dict:
    """Report whether this session can be controlled (X11/XWayland)."""
    session_type = (os.environ.get("XDG_SESSION_TYPE") or "").strip().lower()
    display = (os.environ.get("DISPLAY") or "").strip()
    wayland_display = (os.environ.get("WAYLAND_DISPLAY") or "").strip()

    if display:
        note = (
            "Wayland session with XWayland: X11 clients can be controlled."
            if session_type == "wayland"
            else "X11 display found."
        )
        return {
            "supported": True,
            "type": session_type or "x11",
            "display": display,
            "wayland_display": wayland_display,
            "note": note,
        }
    if session_type == "wayland":
        return {
            "supported": False,
            "type": "wayland",
            "display": "",
            "wayland_display": wayland_display,
            "note": "Native Wayland without XWayland is not supported. Log in with an X11 "
            "or XWayland session, or run the node in the optional virtual-desktop container.",
        }
    return {
        "supported": False,
        "type": session_type or "unknown",
        "display": "",
        "wayland_display": wayland_display,
        "note": f"No graphical session detected on {platform.system()}; start the node from "
        "inside a desktop session.",
    }


__all__ = [
    "ConfigError",
    "DEFAULT_CONTROL_PORT",
    "NODE_WS_PATH",
    "NodeConfig",
    "normalise_server_url",
    "read_token",
    "session_info",
]
