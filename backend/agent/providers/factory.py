from typing import Optional
from .base import ModelProvider
from .gemini import GeminiProvider
from .openai import OpenAIProvider
from .anthropic import AnthropicProvider
from .deepseek import DeepSeekProvider
from .ollama import OllamaProvider
from .mock import MockProvider

def get_provider(
    name: str,
    api_key: Optional[str] = None,
    model: Optional[str] = None,
    base_url: Optional[str] = None,
    simulated_cost: float = 0.05
) -> ModelProvider:
    provider_key = name.lower()
    if provider_key == "mock":
        return MockProvider(simulated_cost_per_step=simulated_cost)
    elif provider_key == "gemini":
        return GeminiProvider(api_key=api_key or "", model=model or "gemini-1.5-flash")
    elif provider_key == "openai":
        return OpenAIProvider(api_key=api_key or "", model=model or "gpt-4o")
    elif provider_key == "anthropic":
        return AnthropicProvider(api_key=api_key or "", model=model or "claude-3-5-sonnet-20241022")
    elif provider_key == "deepseek":
        return DeepSeekProvider(api_key=api_key or "", model=model or "deepseek-chat")
    elif provider_key == "ollama":
        return OllamaProvider(base_url=base_url or "http://localhost:11434", model=model or "llama3.2-vision")
    else:
        raise ValueError(f"Unknown provider '{name}'")
