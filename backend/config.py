import base64
import io
import os
import secrets
from pathlib import Path
from typing import Optional

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
    SECRET_KEY: str = secrets.token_hex(32)
    ENCRYPTION_KEY: Optional[str] = None  # 32-byte urlsafe base64 key for Fernet
    DATA_DIR: str = "./data"
    DATABASE_URL: Optional[str] = None

    # Computer control
    COMPUTER: str = "desktop"  # desktop | simulated
    SCREEN_MAX_WIDTH: int = 1280
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

    @property
    def data_path(self) -> Path:
        return Path(os.environ.get("OPEN_COMPUTR_USE_DATA_DIR", self.DATA_DIR)).expanduser()

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
