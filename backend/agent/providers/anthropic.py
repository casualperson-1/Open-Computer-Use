import base64
from typing import List, Dict, Any, Optional
import httpx
from .base import ModelProvider, ProviderCapabilities, ModelResponse

class AnthropicProvider(ModelProvider):
    def __init__(self, api_key: str, model: str = "claude-3-5-sonnet-20241022"):
        self.api_key = api_key
        self.model = model

    def get_capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            has_vision=True,
            supports_tools=True,
            max_context_tokens=200000,
            cost_per_1k_input=0.003,
            cost_per_1k_output=0.015
        )

    async def chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        image_bytes: Optional[bytes] = None
    ) -> ModelResponse:
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json"
        }
        
        system_text = ""
        claude_msgs = []
        for m in messages:
            if m["role"] == "system":
                system_text += m["content"] + "\n"
            else:
                claude_msgs.append({"role": m["role"], "content": m["content"]})

        if image_bytes and claude_msgs:
            last = claude_msgs[-1]
            b64_img = base64.b64encode(image_bytes).decode("utf-8")
            last["content"] = [
                {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64_img}},
                {"type": "text", "text": last["content"]}
            ]

        payload: Dict[str, Any] = {
            "model": self.model,
            "max_tokens": 1024,
            "system": system_text.strip(),
            "messages": claude_msgs,
        }

        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post("https://api.anthropic.com/v1/messages", headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()

        content_blocks = data.get("content", [])
        text = "".join([b.get("text", "") for b in content_blocks if b.get("type") == "text"])
        
        usage = data.get("usage", {})
        tin = usage.get("input_tokens", 0)
        tout = usage.get("output_tokens", 0)
        cost = (tin / 1000.0) * 0.003 + (tout / 1000.0) * 0.015

        return ModelResponse(
            content=text,
            tokens_in=tin,
            tokens_out=tout,
            estimated_cost=cost
        )
