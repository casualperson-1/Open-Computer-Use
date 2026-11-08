"""DeepSeek adapter (OpenAI-compatible, text-only chat models)."""

from __future__ import annotations

from .openai_compat import OpenAICompatibleProvider


class DeepSeekProvider(OpenAICompatibleProvider):
    name = "deepseek"
    label = "DeepSeek"
    default_model = "deepseek-chat"
    default_base_url = "https://api.deepseek.com/v1"
    # deepseek-chat / deepseek-reasoner do not accept image input.
    has_vision = False
    requires_key = True
    context_tokens = 64000
    input_cost_per_1k = 0.00014
    output_cost_per_1k = 0.00028
