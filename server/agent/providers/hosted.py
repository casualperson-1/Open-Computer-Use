"""Hosted OpenAI-compatible providers.

OpenRouter, Groq, NVIDIA NIM, xAI, Mistral, Together, Fireworks, Cerebras and
Perplexity all expose the Chat Completions schema, so each adapter only needs a
base URL, a sensible default model and hints for model-dependent vision support.
Users can override the model (and, for self-hosted gateways, the endpoint) in
Settings.
"""

from __future__ import annotations

from typing import List

from .openai_compat import OpenAICompatibleProvider


class OpenRouterProvider(OpenAICompatibleProvider):
    name = "openrouter"
    label = "OpenRouter"
    default_model = "google/gemini-2.5-flash"
    default_base_url = "https://openrouter.ai/api/v1"
    has_vision = True
    requires_key = True
    context_tokens = 200000
    input_cost_per_1k = 0.0005
    output_cost_per_1k = 0.002

    def _headers(self):
        headers = super()._headers()
        headers.setdefault("HTTP-Referer", "http://localhost:8765")
        headers.setdefault("X-Title", "Open Computer Use")
        return headers


class GroqProvider(OpenAICompatibleProvider):
    name = "groq"
    label = "Groq"
    default_model = "meta-llama/llama-4-scout-17b-16e-instruct"
    default_base_url = "https://api.groq.com/openai/v1"
    has_vision = False
    vision_hints = ("vision", "scout", "maverick")
    requires_key = True
    context_tokens = 131072
    input_cost_per_1k = 0.00011
    output_cost_per_1k = 0.00034


class NvidiaProvider(OpenAICompatibleProvider):
    name = "nvidia"
    label = "NVIDIA NIM"
    default_model = "meta/llama-3.2-90b-vision-instruct"
    default_base_url = "https://integrate.api.nvidia.com/v1"
    has_vision = False
    vision_hints = ("vision", "vl", "llava", "vila", "nemotron-nano-vl")
    requires_key = True
    context_tokens = 128000
    input_cost_per_1k = 0.0002
    output_cost_per_1k = 0.0002


class XAIProvider(OpenAICompatibleProvider):
    name = "xai"
    label = "xAI (Grok)"
    default_model = "grok-4"
    default_base_url = "https://api.x.ai/v1"
    has_vision = True
    requires_key = True
    context_tokens = 256000
    input_cost_per_1k = 0.003
    output_cost_per_1k = 0.015


class MistralProvider(OpenAICompatibleProvider):
    name = "mistral"
    label = "Mistral AI"
    default_model = "pixtral-12b-2409"
    default_base_url = "https://api.mistral.ai/v1"
    has_vision = False
    vision_hints = ("pixtral", "vision", "mistral-small-3", "ministral-3", "magistral")
    requires_key = True
    context_tokens = 128000
    input_cost_per_1k = 0.0002
    output_cost_per_1k = 0.0006


class TogetherProvider(OpenAICompatibleProvider):
    name = "together"
    label = "Together AI"
    default_model = "meta-llama/Llama-3.2-90B-Vision-Instruct-Turbo"
    default_base_url = "https://api.together.xyz/v1"
    has_vision = False
    vision_hints = ("vision", "vl", "llava")
    requires_key = True
    context_tokens = 128000
    input_cost_per_1k = 0.00018
    output_cost_per_1k = 0.00018


class FireworksProvider(OpenAICompatibleProvider):
    name = "fireworks"
    label = "Fireworks AI"
    default_model = "accounts/fireworks/models/llama-v3p2-90b-vision-instruct"
    default_base_url = "https://api.fireworks.ai/inference/v1"
    has_vision = False
    vision_hints = ("vision", "vl", "llava")
    requires_key = True
    context_tokens = 128000
    input_cost_per_1k = 0.0009
    output_cost_per_1k = 0.0009


class CerebrasProvider(OpenAICompatibleProvider):
    name = "cerebras"
    label = "Cerebras"
    default_model = "llama-3.3-70b"
    default_base_url = "https://api.cerebras.ai/v1"
    has_vision = False
    requires_key = True
    context_tokens = 131072
    input_cost_per_1k = 0.00085
    output_cost_per_1k = 0.0012


class PerplexityProvider(OpenAICompatibleProvider):
    name = "perplexity"
    label = "Perplexity"
    default_model = "sonar"
    default_base_url = "https://api.perplexity.ai"
    has_vision = False
    requires_key = True
    context_tokens = 128000
    input_cost_per_1k = 0.001
    output_cost_per_1k = 0.001

    # Perplexity does not expose a public models endpoint.
    KNOWN_MODELS = (
        "sonar",
        "sonar-pro",
        "sonar-reasoning",
        "sonar-reasoning-pro",
        "sonar-deep-research",
    )

    async def list_models(self) -> List[str]:
        return list(self.KNOWN_MODELS)
