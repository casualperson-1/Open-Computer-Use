"""Google Gemini generateContent adapter with inline images and function calls."""

from __future__ import annotations

import base64
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit

from .base import ChatMessage, ModelProvider, ModelTurn, ProviderError, ToolCall, ToolSpec


class GeminiProvider(ModelProvider):
    name = "gemini"
    label = "Google Gemini"
    default_model = "gemini-2.5-flash"
    default_base_url = "https://generativelanguage.googleapis.com/v1beta"
    has_vision = True
    requires_key = True
    context_tokens = 1000000
    input_cost_per_1k = 0.0003
    output_cost_per_1k = 0.0025

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        # A host-only override (no /v1beta) is ambiguous but almost always
        # means the standard API root; fill it in instead of requesting
        # https://host/models/... and getting a 404.
        if not urlsplit(self.base_url).path.strip("/"):
            self.base_url = f"{self.base_url}/v1beta"

    def _headers(self) -> Dict[str, str]:
        return {"Content-Type": "application/json", "x-goog-api-key": self.api_key}

    def _encode(
        self, messages: List[ChatMessage]
    ) -> tuple[str, List[Dict[str, Any]]]:
        system_parts: List[str] = []
        contents: List[Dict[str, Any]] = []

        def add(role: str, parts: List[Dict[str, Any]]) -> None:
            # Gemini expects alternating user/model turns; merge adjacent parts
            # (for example several tool results plus the next screenshot).
            if contents and contents[-1]["role"] == role:
                contents[-1]["parts"].extend(parts)
            else:
                contents.append({"role": role, "parts": parts})

        for message in messages:
            if message.role == "system":
                if message.content:
                    system_parts.append(message.content)
                continue

            if message.role == "tool":
                add(
                    "user",
                    [
                        {
                            "functionResponse": {
                                "name": message.name or "tool",
                                "response": {"content": message.content},
                            }
                        }
                    ],
                )
                continue

            parts: List[Dict[str, Any]] = []
            if message.role == "assistant":
                if message.content:
                    parts.append({"text": message.content})
                for call in message.tool_calls:
                    part: Dict[str, Any] = {
                        "functionCall": {"name": call.name, "args": call.arguments}
                    }
                    if call.thought_signature:
                        part["thoughtSignature"] = call.thought_signature
                    parts.append(part)
                if not parts:
                    parts.append({"text": "(no output)"})
                add("model", parts)
                continue

            for image in message.images:
                if self.has_vision:
                    parts.append(
                        {
                            "inlineData": {
                                "mimeType": "image/png",
                                "data": base64.b64encode(image).decode("ascii"),
                            }
                        }
                    )
            if message.content:
                parts.append({"text": message.content})
            if not parts:
                parts.append({"text": "(empty)"})
            add("user", parts)

        return "\n\n".join(system_parts).strip(), contents

    def build_payload(
        self,
        messages: List[ChatMessage],
        tools: Optional[List[ToolSpec]] = None,
    ) -> Dict[str, Any]:
        system, contents = self._encode(messages)
        payload: Dict[str, Any] = {
            "contents": contents,
            "generationConfig": {
                "temperature": self.temperature,
                "maxOutputTokens": self.max_tokens,
            },
        }
        if system:
            payload["systemInstruction"] = {"parts": [{"text": system}]}
        if tools:
            payload["tools"] = [{"functionDeclarations": [tool.gemini_dict() for tool in tools]}]
            payload["toolConfig"] = {"functionCallingConfig": {"mode": "AUTO"}}
        return payload

    def parse_response(self, data: Dict[str, Any]) -> ModelTurn:
        candidates = data.get("candidates") or []
        if not candidates:
            feedback = data.get("promptFeedback") or {}
            reason = feedback.get("blockReason")
            raise ProviderError(
                "Gemini returned no candidates" + (f" (blocked: {reason})" if reason else ".")
            )

        parts = (candidates[0].get("content") or {}).get("parts") or []
        text_parts: List[str] = []
        tool_calls: List[ToolCall] = []

        for index, part in enumerate(parts):
            if "text" in part and part["text"]:
                text_parts.append(part["text"])
            call = part.get("functionCall")
            if call:
                arguments = call.get("args")
                if not isinstance(arguments, dict):
                    arguments = {}
                signature = part.get("thoughtSignature")
                tool_calls.append(
                    ToolCall(
                        id=f"call_{index}",
                        name=str(call.get("name") or ""),
                        arguments=arguments,
                        thought_signature=str(signature) if signature else None,
                    )
                )

        usage = data.get("usageMetadata") or {}
        tokens_in = int(usage.get("promptTokenCount") or 0)
        tokens_out = int(usage.get("candidatesTokenCount") or 0)

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
        url = f"{self.base_url}/models/{self.model}:generateContent"
        data = await self._post_json(url, self.build_payload(messages, tools), self._headers())
        return self.parse_response(data)

    async def list_models(self) -> List[str]:
        data = await self._get_json(f"{self.base_url}/models", self._headers())
        names = []
        for model in data.get("models") or []:
            if not isinstance(model, dict):
                continue
            name = str(model.get("name") or "").removeprefix("models/")
            methods = model.get("supportedGenerationMethods") or []
            if name and (not methods or "generateContent" in methods):
                names.append(name)
        return sorted(set(names))
