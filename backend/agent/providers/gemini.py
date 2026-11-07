import base64
import json
from typing import List, Dict, Any, Optional
import httpx
from .base import ModelProvider, ProviderCapabilities, ModelResponse

class GeminiProvider(ModelProvider):
    def __init__(self, api_key: str, model: str = "gemini-1.5-flash"):
        self.api_key = api_key
        self.model = model
        self.endpoint = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent?key={self.api_key}"

    def get_capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            has_vision=True,
            supports_tools=True,
            max_context_tokens=1000000,
            cost_per_1k_input=0.000075,
            cost_per_1k_output=0.00030
        )

    async def chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        image_bytes: Optional[bytes] = None
    ) -> ModelResponse:
        contents = []
        for msg in messages:
            role = "user" if msg["role"] in ("user", "system") else "model"
            parts = [{"text": msg["content"]}]
            contents.append({"role": role, "parts": parts})

        # Append image to latest user prompt if available
        if image_bytes and contents:
            last_parts = contents[-1]["parts"]
            b64_img = base64.b64encode(image_bytes).decode("utf-8")
            last_parts.append({
                "inline_data": {
                    "mime_type": "image/png",
                    "data": b64_img
                }
            })

        payload = {"contents": contents}

        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(self.endpoint, json=payload)
            resp.raise_for_status()
            data = resp.json()

        candidates = data.get("candidates", [])
        content = ""
        if candidates:
            parts = candidates[0].get("content", {}).get("parts", [])
            content = "".join([p.get("text", "") for p in parts])

        usage = data.get("usageMetadata", {})
        tin = usage.get("promptTokenCount", 0)
        tout = usage.get("candidatesTokenCount", 0)
        cost = (tin / 1000.0) * 0.000075 + (tout / 1000.0) * 0.00030

        return ModelResponse(
            content=content,
            tool_calls=[],
            tokens_in=tin,
            tokens_out=tout,
            estimated_cost=cost
        )
