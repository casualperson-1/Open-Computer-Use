import json
from typing import List, Dict, Any, Optional
import httpx
from .base import ModelProvider, ProviderCapabilities, ModelResponse

class DeepSeekProvider(ModelProvider):
    def __init__(self, api_key: str, model: str = "deepseek-chat", base_url: str = "https://api.deepseek.com/v1"):
        self.api_key = api_key
        self.model = model
        self.base_url = base_url

    def get_capabilities(self) -> ProviderCapabilities:
        # Deepseek chat is a fast text-only model with tool calling
        return ProviderCapabilities(
            has_vision=False,
            supports_tools=True,
            max_context_tokens=64000,
            cost_per_1k_input=0.00014,
            cost_per_1k_output=0.00028
        )

    async def chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        image_bytes: Optional[bytes] = None
    ) -> ModelResponse:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.2
        }
        if tools:
            payload["tools"] = tools

        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(f"{self.base_url}/chat/completions", headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()

        choice = data["choices"][0]["message"]
        content = choice.get("content") or ""
        tool_calls = choice.get("tool_calls", [])
        
        usage = data.get("usage", {})
        tin = usage.get("prompt_tokens", 0)
        tout = usage.get("completion_tokens", 0)
        cost = (tin / 1000.0) * 0.00014 + (tout / 1000.0) * 0.00028

        return ModelResponse(
            content=content,
            tool_calls=tool_calls,
            tokens_in=tin,
            tokens_out=tout,
            estimated_cost=cost
        )
