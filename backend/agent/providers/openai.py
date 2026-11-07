import base64
from typing import List, Dict, Any, Optional
import httpx
from .base import ModelProvider, ProviderCapabilities, ModelResponse

class OpenAIProvider(ModelProvider):
    def __init__(self, api_key: str, model: str = "gpt-4o", base_url: str = "https://api.openai.com/v1"):
        self.api_key = api_key
        self.model = model
        self.base_url = base_url

    def get_capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            has_vision=True,
            supports_tools=True,
            max_context_tokens=128000,
            cost_per_1k_input=0.0025,
            cost_per_1k_output=0.010
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
        
        # Inject vision payload if image supplied
        formatted_messages = list(messages)
        if image_bytes and formatted_messages:
            last = formatted_messages[-1]
            b64_img = base64.b64encode(image_bytes).decode("utf-8")
            if isinstance(last.get("content"), str):
                last["content"] = [
                    {"type": "text", "text": last["content"]},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64_img}"}}
                ]

        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": formatted_messages,
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
        cost = (tin / 1000.0) * 0.0025 + (tout / 1000.0) * 0.010

        return ModelResponse(
            content=content,
            tool_calls=tool_calls,
            tokens_in=tin,
            tokens_out=tout,
            estimated_cost=cost
        )
