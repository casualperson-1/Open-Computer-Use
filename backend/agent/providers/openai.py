"""OpenAI adapter built on the shared OpenAI-compatible implementation."""

from __future__ import annotations

from .openai_compat import OpenAICompatibleProvider


class OpenAIProvider(OpenAICompatibleProvider):
    name = "openai"
    label = "OpenAI"
    default_model = "gpt-4o"
    default_base_url = "https://api.openai.com/v1"
    has_vision = True
    requires_key = True
    context_tokens = 128000
    input_cost_per_1k = 0.0025
    output_cost_per_1k = 0.01
