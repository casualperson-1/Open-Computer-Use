"""Provider registry for BYOK model selection."""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from .anthropic import AnthropicProvider
from .base import ModelProvider, ProviderError, ToolSpec
from .deepseek import DeepSeekProvider
from .gemini import GeminiProvider
from .mock import MockProvider, ScriptedProvider
from .ollama import OllamaProvider
from .openai import OpenAIProvider
from .openai_compat import OpenAICompatibleProvider

PROVIDER_CLASSES = {
    "anthropic": AnthropicProvider,
    "openai": OpenAIProvider,
    "gemini": GeminiProvider,
    "deepseek": DeepSeekProvider,
    "ollama": OllamaProvider,
    "openai-compatible": OpenAICompatibleProvider,
    "mock": MockProvider,
}

ALIASES = {
    "claude": "anthropic",
    "google": "gemini",
    "openai_compatible": "openai-compatible",
    "compatible": "openai-compatible",
    "custom": "openai-compatible",
    "openrouter": "openai-compatible",
    "groq": "openai-compatible",
    "together": "openai-compatible",
    "lmstudio": "openai-compatible",
    "lm-studio": "openai-compatible",
    "vllm": "openai-compatible",
    "local": "ollama",
}

BASE_URL_ENV = {
    "anthropic": "ANTHROPIC_BASE_URL",
    "openai": "OPENAI_BASE_URL",
    "gemini": "GEMINI_BASE_URL",
    "deepseek": "DEEPSEEK_BASE_URL",
    "ollama": "OLLAMA_BASE_URL",
    "openai-compatible": "OPENAI_COMPATIBLE_BASE_URL",
}


def normalise_provider(name: str) -> str:
    key = (name or "").strip().lower()
    return ALIASES.get(key, key)


def get_provider(
    name: str,
    api_key: Optional[str] = None,
    model: Optional[str] = None,
    base_url: Optional[str] = None,
    max_tokens: int = 4096,
    vision: Optional[bool] = None,
    **kwargs: Any,
) -> ModelProvider:
    key = normalise_provider(name)
    provider_class = PROVIDER_CLASSES.get(key)
    if provider_class is None:
        raise ValueError(f"Unknown provider '{name}'.")

    resolved_base = base_url or os.environ.get(BASE_URL_ENV.get(key, ""), "") or None
    resolved_key = api_key if api_key is not None else os.environ.get(f"{key.upper().replace('-', '_')}_API_KEY", "")

    init: Dict[str, Any] = {
        "model": model or None,
        "api_key": resolved_key or "",
        "base_url": resolved_base,
        "max_tokens": max_tokens,
    }
    if key in ("openai-compatible", "ollama"):
        init["vision"] = vision
    return provider_class(**init)


def available_providers() -> List[Dict[str, Any]]:
    catalog: List[Dict[str, Any]] = []
    for key in ("anthropic", "openai", "gemini", "deepseek", "ollama", "openai-compatible"):
        provider_class = PROVIDER_CLASSES[key]
        instance = provider_class()
        entry = instance.metadata()
        entry["key_env"] = f"{key.upper().replace('-', '_')}_API_KEY"
        catalog.append(entry)
    return catalog


def provider_env_key(name: str) -> str:
    return f"{normalise_provider(name).upper().replace('-', '_')}_API_KEY"


__all__ = [
    "PROVIDER_CLASSES",
    "available_providers",
    "get_provider",
    "normalise_provider",
    "provider_env_key",
    "ModelProvider",
    "ProviderError",
    "ToolSpec",
    "MockProvider",
    "ScriptedProvider",
]
