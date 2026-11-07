import os
import secrets
from typing import Optional
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    # App
    APP_ENV: str = "production"
    SECRET_KEY: str = secrets.token_hex(32)
    ENCRYPTION_KEY: Optional[str] = None # 32-byte urlsafe base64 key for Fernet
    PORT: int = 8000
    
    # Auth
    ADMIN_USERNAME: str = "admin"
    ADMIN_PASSWORD_HASH: Optional[str] = None
    
    # Browser
    VIEWPORT_WIDTH: int = 1280
    VIEWPORT_HEIGHT: int = 720
    BROWSER_HEADLESS: bool = False
    BROWSER_FAST_MODE: bool = False
    BROWSER_REAL_MOUSE: bool = False
    BROWSER_USER_DATA_DIR: str = "/app/data/browser_profile"
    
    # Models & Defaults
    DEFAULT_PROVIDER: str = "gemini" # anthropic, gemini, openai, deepseek, ollama
    DEFAULT_MODEL: str = "gemini-1.5-flash"
    OLLAMA_BASE_URL: str = "http://localhost:11434"
    
    # Database
    DATABASE_URL: str = "sqlite+aiosqlite:////app/data/agent.db"
    
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"

settings = Settings()
