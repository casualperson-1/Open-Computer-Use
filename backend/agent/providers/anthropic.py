"""Anthropic Messages API adapter with vision and tool use."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .base import (
    ChatMessage,
    ModelProvider,
    ModelTurn,
    ProviderError,
    ToolCall,
    ToolSpec,
    image_block_anthropic,
)


class AnthropicProvider(ModelProvider):
    name = "anthropic"
    label = "Anthropic (Claude)"
    default_model = "claude-sonnet-4-5"
    default_base_url = "https://api.anthropic.com"
    has_vision = True
    requires_key = True
    context_tokens = 200000
    input_cost_per_1k = 0.003
    output_cost_per_1k = 0.015

    def _headers(self) -> Dict[str, str]:
        return {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }

    def _encode(
        self, messages: List[ChatMessage]
    ) -> tuple[str, List[Dict[str, Any]]]:
        system_parts: List[str] = []
        encoded: List[Dict[str, Any]] = []
        last_role: Optional[str] = None
        last_was_tool = False

        for message in messages:
            if message.role == "system":
                if message.content:
                    system_parts.append(message.content)
                continue

            if message.role == "tool":
                block: Dict[str, Any] = {
                    "type": "tool_result",
                    "tool_use_id": message.tool_call_id or "call_0",
                    "content": message.content,
                }
                if message.is_error:
                    block["is_error"] = True
                if encoded and encoded[-1]["role"] == "user" and last_was_tool:
                    encoded[-1]["content"].append(block)
                else:
                    encoded.append({"role": "user", "content": [block]})
                last_role, last_was_tool = "user", True
                continue

            blocks: List[Dict[str, Any]] = []
            if message.role == "assistant":
                if message.content:
                    blocks.append({"type": "text", "text": message.content})
                for call in message.tool_calls:
                    blocks.append(
                        {
                            "type": "tool_use",
                            "id": call.id,
                            "name": call.name,
                            "input": call.arguments,
                        }
                    )
                if not blocks:
                    blocks.append({"type": "text", "text": "(no output)"})
                encoded.append({"role": "assistant", "content": blocks})
                last_role, last_was_tool = "assistant", False
                continue

            # user message
            for image in message.images:
                if self.has_vision:
                    blocks.append(image_block_anthropic(image))
            if message.content:
                blocks.append({"type": "text", "text": message.content})
            if not blocks:
                blocks.append({"type": "text", "text": "(empty)"})

            if encoded and encoded[-1]["role"] == "user":
                encoded[-1]["content"].extend(blocks)
            else:
                encoded.append({"role": "user", "content": blocks})
            last_role, last_was_tool = "user", False

        if not encoded:
            encoded = [{"role": "user", "content": [{"type": "text", "text": "(start)"}]}]
        return "\n\n".join(system_parts).strip(), encoded

    def build_payload(
        self,
        messages: List[ChatMessage],
        tools: Optional[List[ToolSpec]] = None,
    ) -> Dict[str, Any]:
        system, encoded = self._encode(messages)
        payload: Dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "messages": encoded,
        }
        if system:
            payload["system"] = system
        if tools:
            payload["tools"] = [tool.anthropic_dict() for tool in tools]
        return payload

    def parse_response(self, data: Dict[str, Any]) -> ModelTurn:
        text_parts: List[str] = []
        tool_calls: List[ToolCall] = []

        for index, block in enumerate(data.get("content") or []):
            block_type = block.get("type")
            if block_type == "text":
                text_parts.append(block.get("text", ""))
            elif block_type == "tool_use":
                arguments = block.get("input")
                if not isinstance(arguments, dict):
                    arguments = {}
                tool_calls.append(
                    ToolCall(
                        id=str(block.get("id") or f"toolu_{index}"),
                        name=str(block.get("name") or ""),
                        arguments=arguments,
                    )
                )

        usage = data.get("usage") or {}
        tokens_in = int(usage.get("input_tokens") or 0)
        tokens_out = int(usage.get("output_tokens") or 0)

        return ModelTurn(
            text="".join(text_parts).strip(),
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
            f"{self.base_url}/v1/messages",
            self.build_payload(messages, tools),
            self._headers(),
        )
        return self.parse_response(data)

    async def list_models(self) -> List[str]:
        data = await self._get_json(f"{self.base_url}/v1/models", self._headers())
        models = data.get("data") or []
        return sorted(
            {
                str(model.get("id"))
                for model in models
                if isinstance(model, dict) and model.get("id")
            }
        )
