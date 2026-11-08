"""Local Ollama adapter. Runs fully offline; images work with vision models."""

from __future__ import annotations

import base64
from typing import Any, Dict, List, Optional

from .base import ChatMessage, ModelProvider, ModelTurn, ProviderError, ToolCall, ToolSpec

VISION_HINTS = (
    "vision",
    "llava",
    "bakllava",
    "moondream",
    "minicpm-v",
    "qwen2-vl",
    "qwen2.5vl",
    "gemma3",
    "mistral-small3",
)


class OllamaProvider(ModelProvider):
    name = "ollama"
    label = "Ollama (local)"
    default_model = "qwen2.5vl:7b"
    default_base_url = "http://localhost:11434"
    has_vision = True
    requires_key = False
    context_tokens = 32000
    input_cost_per_1k = 0.0
    output_cost_per_1k = 0.0

    def __init__(self, *args: Any, vision: Optional[bool] = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        if vision is None:
            lowered = self.model.lower()
            vision = any(hint in lowered for hint in VISION_HINTS)
        self.has_vision = bool(vision)

    def build_payload(
        self,
        messages: List[ChatMessage],
        tools: Optional[List[ToolSpec]] = None,
    ) -> Dict[str, Any]:
        encoded: List[Dict[str, Any]] = []
        for message in messages:
            entry: Dict[str, Any] = {"role": message.role, "content": message.content}

            if message.role == "assistant" and message.tool_calls:
                entry["tool_calls"] = [
                    {"function": {"name": call.name, "arguments": call.arguments}}
                    for call in message.tool_calls
                ]
            if message.role == "tool":
                entry["role"] = "tool"
                if message.name:
                    entry["tool_name"] = message.name
            if message.images and self.has_vision:
                entry["images"] = [
                    base64.b64encode(image).decode("ascii") for image in message.images
                ]
            encoded.append(entry)

        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": encoded,
            "stream": False,
            "options": {
                "temperature": self.temperature,
                "num_predict": self.max_tokens,
            },
        }
        if tools:
            payload["tools"] = [tool.openai_dict() for tool in tools]
        return payload

    def parse_response(self, data: Dict[str, Any]) -> ModelTurn:
        message = data.get("message") or {}
        content = message.get("content") or ""

        tool_calls: List[ToolCall] = []
        for index, raw in enumerate(message.get("tool_calls") or []):
            function = raw.get("function") or {}
            arguments = function.get("arguments")
            if not isinstance(arguments, dict):
                arguments = {}
            tool_calls.append(
                ToolCall(
                    id=str(raw.get("id") or f"call_{index}"),
                    name=str(function.get("name") or ""),
                    arguments=arguments,
                )
            )

        tokens_in = int(data.get("prompt_eval_count") or 0)
        tokens_out = int(data.get("eval_count") or 0)
        return ModelTurn(
            text=content.strip(),
            tool_calls=tool_calls,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            estimated_cost=0.0,
        )

    async def generate(
        self,
        messages: List[ChatMessage],
        tools: Optional[List[ToolSpec]] = None,
    ) -> ModelTurn:
        data = await self._post_json(
            f"{self.base_url}/api/chat", self.build_payload(messages, tools)
        )
        if data.get("error"):
            raise ProviderError(f"Ollama error: {data['error']}")
        return self.parse_response(data)

    async def list_models(self) -> List[str]:
        data = await self._get_json(f"{self.base_url}/api/tags")
        return sorted(
            {
                str(model.get("name"))
                for model in data.get("models") or []
                if isinstance(model, dict) and model.get("name")
            }
        )
