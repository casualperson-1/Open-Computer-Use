import os
import secrets
from pathlib import Path
from typing import Optional

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration. Everything can be set in .env or the environment."""

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # App
    APP_ENV: str = "production"
    HOST: str = "127.0.0.1"
    PORT: int = 8765
    # Exposing the server off-host is two explicit steps: an owner must exist
    # (created with `python -m server.cli create-owner`) and this must be
    # enabled. Both are enforced before the socket is bound.
    ALLOW_REMOTE_BIND: bool = False
    # Public https origin when a proxy or tunnel terminates TLS, e.g.
    # https://agent.example.com. Used for cookie security and Origin checks.
    PUBLIC_BASE_URL: Optional[str] = None
    # Comma-separated CIDRs whose X-Forwarded-* headers are believed.
    # Empty means: trust no proxy, i.e. read the socket peer only.
    TRUSTED_PROXIES: str = ""
    # SECRET_KEY can also come from a mounted file (Docker secret), which is
    # preferred over the environment so it does not show up in `docker inspect`.
    SECRET_KEY: str = ""
    SECRET_KEY_FILE: Optional[str] = None
    ENCRYPTION_KEY: Optional[str] = None  # urlsafe base64 Fernet key
    ENCRYPTION_KEY_FILE: Optional[str] = None  # mounted secret file (mode 600)
    DATA_DIR: str = "./data"
    DATABASE_URL: Optional[str] = None
    BACKUP_DIR: str = "./backups"

    # Login protection: N failures inside the window trigger an exponential
    # lockout (per IP, per username and globally).
    LOGIN_MAX_FAILURES: int = 5
    LOGIN_WINDOW_SECONDS: int = 300
    LOGIN_LOCKOUT_BASE_SECONDS: float = 5.0
    LOGIN_LOCKOUT_MAX_SECONDS: float = 900.0
    # Sign-in is remembered for 90 days and renewed while the browser is used.
    SESSION_TTL_HOURS: int = 2160

    # Whether anyone can create an account from the sign-in page. "open" (the
    # default) is the normal mode; "closed" disables signup. There are no invite
    # codes: a legacy "invite" value is treated as open.
    SIGNUP_MODE: str = "open"
    # Per-user limits for new accounts (the admin is unlimited).
    USER_RUN_LIMIT_PER_HOUR: int = 30
    USER_USAGE_CAP_USD: Optional[float] = None

    @field_validator("USER_RUN_LIMIT_PER_HOUR", "USER_USAGE_CAP_USD", mode="before")
    @classmethod
    def _blank_user_limits(cls, value, info):
        """Compose passes unset optional limits as ""; treat that as unset."""
        if value is None or (isinstance(value, str) and not value.strip()):
            return 30 if info.field_name == "USER_RUN_LIMIT_PER_HOUR" else None
        return value

    # Desktop nodes. Real mouse/keyboard control always runs on a node that
    # dials out to this server; the server itself never opens an inbound port
    # on the controlled machine.
    # required = refuse desktop runs without this user's own node (default)
    # auto     = use a connected node, fall back to the simulated desktop
    # never    = always use the simulated desktop
    REMOTE_NODE_MODE: str = "required"
    NODE_COMMAND_TIMEOUT: float = 45.0

    # Policy applied on the server before anything is sent to a node. The node
    # enforces its own policy too; deny lists always win over allow lists here.
    NODE_ALLOWED_COMMANDS: str = ""
    NODE_DENIED_COMMANDS: str = ""
    NODE_ALLOWED_DOMAINS: str = ""
    NODE_DENIED_DOMAINS: str = ""
    NODE_ALLOWED_APPS: str = ""
    NODE_DENIED_APPS: str = ""
    NODE_RATE_LIMIT: int = 240  # commands per minute per desktop run (0 = off)
    NODE_AUDIT: bool = True
    # off = never ask, risky = ask for NODE_APPROVAL_COMMANDS, all = ask always
    NODE_APPROVAL_MODE: str = "off"
    NODE_APPROVAL_COMMANDS: str = "open_item,open_url,browser_action,key_combo"
    NODE_APPROVAL_TIMEOUT: float = 120.0

    # Computer control
    COMPUTER: str = "desktop"  # desktop | simulated
    SCREEN_MAX_WIDTH: int = 1280
    # Live view: JPEG frames the node pushes while the screen panel is open.
    LIVE_VIEW_FPS: float = 2.0
    LIVE_VIEW_QUALITY: int = 62
    HUMAN_MOUSE: bool = True
    MAX_STEPS: int = 30
    MAX_COST: float = 1.0
    APPROVAL_MODE: str = "auto"  # auto | confirm
    VNC_URL: Optional[str] = None

    # Model defaults (keys live in the encrypted store or environment)
    DEFAULT_PROVIDER: str = "anthropic"
    DEFAULT_MODEL: str = ""
    MAX_TOKENS: int = 4096
    OLLAMA_BASE_URL: str = "http://localhost:11434"
    OPENAI_BASE_URL: Optional[str] = None
    OPENAI_COMPATIBLE_BASE_URL: Optional[str] = None
    OPENAI_COMPATIBLE_VISION: bool = True
    ANTHROPIC_BASE_URL: Optional[str] = None
    GEMINI_BASE_URL: Optional[str] = None
    DEEPSEEK_BASE_URL: Optional[str] = None

    # Browser-agent compatibility (legacy modules)
    VIEWPORT_WIDTH: int = 1280
    VIEWPORT_HEIGHT: int = 720
    BROWSER_HEADLESS: bool = False
    BROWSER_FAST_MODE: bool = False
    BROWSER_REAL_MOUSE: bool = False
    BROWSER_USER_DATA_DIR: str = "./data/browser_profile"

    @model_validator(mode="after")
    def _resolve_secret_key(self) -> "Settings":
        """Load SECRET_KEY from a mounted secret file when no value is set."""
        if not self.SECRET_KEY and self.SECRET_KEY_FILE:
            path = Path(self.SECRET_KEY_FILE).expanduser()
            if not path.exists():
                raise ValueError(
                    f"SECRET_KEY_FILE points to {path}, which does not exist. "
                    "Mount the secret file or set SECRET_KEY."
                )
            try:
                value = path.read_text(encoding="utf-8").strip()
            except OSError as exc:
                raise ValueError(f"Could not read SECRET_KEY_FILE {path}: {exc}") from exc
            if len(value) < 32:
                raise ValueError(
                    f"SECRET_KEY_FILE ({path}) must contain at least 32 characters."
                )
            self.SECRET_KEY = value
        if not self.SECRET_KEY:
            self.SECRET_KEY = secrets.token_hex(32)
        return self

    @property
    def data_path(self) -> Path:
        return Path(os.environ.get("OPEN_COMPUTER_USE_DATA_DIR", self.DATA_DIR)).expanduser()

    @property
    def backup_path(self) -> Path:
        return Path(self.BACKUP_DIR).expanduser()

    @property
    def database_file(self) -> Optional[Path]:
        """The SQLite file, when the database is SQLite."""
        url = self.database_url
        if not url.startswith("sqlite") or "///" not in url:
            return None
        path = url.split("///", 1)[1]
        return Path(path).expanduser() if path else None

    @property
    def session_ttl_seconds(self) -> int:
        return max(300, int(self.SESSION_TTL_HOURS) * 3600)

    @property
    def database_url(self) -> str:
        if self.DATABASE_URL:
            return self.DATABASE_URL
        db_path = (self.data_path / "cowork.db").resolve()
        return f"sqlite+aiosqlite:///{db_path}"

    def ensure_data_dir(self) -> Path:
        path = self.data_path
        path.mkdir(parents=True, exist_ok=True)
        return path


settings = Settings()
