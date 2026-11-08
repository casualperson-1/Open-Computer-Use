"""Adapter for OpenAI's Chat Completions API and every compatible endpoint.

Covers OpenAI, DeepSeek, OpenRouter, Groq, LM Studio, vLLM, llama.cpp server and
any other server that implements ``POST /chat/completions``.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from .base import (
    ChatMessage,
    ModelProvider,
    ModelTurn,
    ProviderError,
    ToolCall,
    ToolSpec,
    image_block_openai,
)


class OpenAICompatibleProvider(ModelProvider):
    name = "openai-compatible"
    label = "OpenAI-compatible"
    default_model = "gpt-4o"
    default_base_url = "https://api.openai.com/v1"
    has_vision = True
    requires_key = True
    context_tokens = 128000
    input_cost_per_1k = 0.0025
    output_cost_per_1k = 0.01

    def __init__(self, *args: Any, vision: Optional[bool] = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        if vision is not None:
            self.has_vision = vision

    def _headers(self) -> Dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _encode_messages(self, messages: List[ChatMessage]) -> List[Dict[str, Any]]:
        encoded: List[Dict[str, Any]] = []

        for message in messages:
            if message.role == "tool":
                encoded.append(
                    {
                        "role": "tool",
                        "tool_call_id": message.tool_call_id or "call_0",
                        "content": message.content,
                    }
                )
                continue

            if message.role == "assistant":
                entry: Dict[str, Any] = {
                    "role": "assistant",
                    "content": message.content or None,
                }
                if message.tool_calls:
                    entry["tool_calls"] = [
                        {
                            "id": call.id,
                            "type": "function",
                            "function": {
                                "name": call.name,
                                "arguments": json.dumps(call.arguments),
                            },
                        }
                        for call in message.tool_calls
                    ]
                encoded.append(entry)
                continue

            if message.images and self.has_vision:
                blocks: List[Dict[str, Any]] = []
                if message.content:
                    blocks.append({"type": "text", "text": message.content})
                blocks.extend(image_block_openai(image) for image in message.images)
                encoded.append({"role": message.role, "content": blocks})
            else:
                encoded.append({"role": message.role, "content": message.content})

        return encoded

    def build_payload(
        self,
        messages: List[ChatMessage],
        tools: Optional[List[ToolSpec]] = None,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": self._encode_messages(messages),
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "stream": False,
        }
        if tools:
            payload["tools"] = [tool.openai_dict() for tool in tools]
            payload["tool_choice"] = "auto"
        return payload

    def parse_response(self, data: Dict[str, Any]) -> ModelTurn:
        choices = data.get("choices") or []
        if not choices:
            raise ProviderError(f"{self.label} returned no choices.")
        message = choices[0].get("message") or {}

        content = message.get("content") or ""
        if isinstance(content, list):
            content = "".join(
                part.get("text", "") for part in content if isinstance(part, dict)
            )

        tool_calls: List[ToolCall] = []
        for index, raw in enumerate(message.get("tool_calls") or []):
            function = raw.get("function") or {}
            arguments = function.get("arguments")
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments) if arguments.strip() else {}
                except json.JSONDecodeError:
                    arguments = {"_raw_arguments": arguments}
            if not isinstance(arguments, dict):
                arguments = {}
            tool_calls.append(
                ToolCall(
                    id=str(raw.get("id") or f"call_{index}"),
                    name=str(function.get("name") or ""),
                    arguments=arguments,
                )
            )

        usage = data.get("usage") or {}
        tokens_in = int(usage.get("prompt_tokens") or 0)
        tokens_out = int(usage.get("completion_tokens") or 0)
        if not tokens_in:
            tokens_in = self._estimate_tokens(str(content))
        if not tokens_out:
            tokens_out = self._estimate_tokens(str(content))

        return ModelTurn(
            text=content,
            tool_calls=tool_calls,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            estimated_cost=self._cost(tokens_in, tokens_out),
        )

    async def generate(
        self,
        messages: List[ChatMessage],
        tools: Optional[List[ToolSpec]] = None,
    ) -> ModelTurn:
        data = await self._post_json(
            f"{self.base_url}/chat/completions",
            self.build_payload(messages, tools),
            self._headers(),
        )
        return self.parse_response(data)

    async def list_models(self) -> List[str]:
        data = await self._get_json(f"{self.base_url}/models", self._headers())
        models = data.get("data") or data.get("models") or []
        names = []
        for model in models:
            if isinstance(model, dict):
                name = model.get("id") or model.get("name")
                if name:
                    names.append(str(name))
        return sorted(set(names))
