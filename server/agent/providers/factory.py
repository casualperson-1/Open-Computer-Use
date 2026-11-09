"""Provider registry for BYOK model selection."""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from .anthropic import AnthropicProvider
from .base import ModelProvider, ProviderError, ToolSpec
from .deepseek import DeepSeekProvider
from .gemini import GeminiProvider
from .hosted import (
    CerebrasProvider,
    FireworksProvider,
    GroqProvider,
    MistralProvider,
    NvidiaProvider,
    OpenRouterProvider,
    PerplexityProvider,
    TogetherProvider,
    XAIProvider,
)
from .ollama import OllamaProvider
from .openai import OpenAIProvider
from .openai_compat import OpenAICompatibleProvider

PROVIDER_CLASSES = {
    "anthropic": AnthropicProvider,
    "openai": OpenAIProvider,
    "gemini": GeminiProvider,
    "deepseek": DeepSeekProvider,
    "xai": XAIProvider,
    "openrouter": OpenRouterProvider,
    "groq": GroqProvider,
    "nvidia": NvidiaProvider,
    "mistral": MistralProvider,
    "together": TogetherProvider,
    "fireworks": FireworksProvider,
    "cerebras": CerebrasProvider,
    "perplexity": PerplexityProvider,
    "ollama": OllamaProvider,
    "openai-compatible": OpenAICompatibleProvider,
}

CATALOG_ORDER = (
    "anthropic",
    "openai",
    "gemini",
    "deepseek",
    "xai",
    "openrouter",
    "groq",
    "nvidia",
    "mistral",
    "together",
    "fireworks",
    "cerebras",
    "perplexity",
    "ollama",
    "openai-compatible",
)

ALIASES = {
    "claude": "anthropic",
    "google": "gemini",
    "openai_compatible": "openai-compatible",
    "compatible": "openai-compatible",
    "custom": "openai-compatible",
    "open-router": "openrouter",
    "x-ai": "xai",
    "x.ai": "xai",
    "grok": "xai",
    "nim": "nvidia",
    "nvidia-nim": "nvidia",
    "together-ai": "together",
    "togetherai": "together",
    "mistral-ai": "mistral",
    "fireworks-ai": "fireworks",
    "perplexity-ai": "perplexity",
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
    "xai": "XAI_BASE_URL",
    "openrouter": "OPENROUTER_BASE_URL",
    "groq": "GROQ_BASE_URL",
    "nvidia": "NVIDIA_BASE_URL",
    "mistral": "MISTRAL_BASE_URL",
    "together": "TOGETHER_BASE_URL",
    "fireworks": "FIREWORKS_BASE_URL",
    "cerebras": "CEREBRAS_BASE_URL",
    "perplexity": "PERPLEXITY_BASE_URL",
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
    for key in CATALOG_ORDER:
        provider_class = PROVIDER_CLASSES[key]
        instance = provider_class()
        entry = instance.metadata()
        entry["key_env"] = f"{key.upper().replace('-', '_')}_API_KEY"
        catalog.append(entry)
    return catalog


def provider_requires_key(name: str) -> bool:
    provider_class = PROVIDER_CLASSES.get(normalise_provider(name))
    return bool(provider_class and provider_class.requires_key)


def provider_env_key(name: str) -> str:
    return f"{normalise_provider(name).upper().replace('-', '_')}_API_KEY"


__all__ = [
    "PROVIDER_CLASSES",
    "available_providers",
    "get_provider",
    "normalise_provider",
    "provider_env_key",
    "provider_requires_key",
    "ModelProvider",
    "ProviderError",
    "ToolSpec",
]
