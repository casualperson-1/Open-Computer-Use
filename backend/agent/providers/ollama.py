import base64
from typing import List, Dict, Any, Optional
import httpx
from .base import ModelProvider, ProviderCapabilities, ModelResponse

class OllamaProvider(ModelProvider):
    def __init__(self, base_url: str = "http://localhost:11434", model: str = "llama3.2-vision"):
        self.base_url = base_url.rstrip("/")
        self.model = model

    def get_capabilities(self) -> ProviderCapabilities:
        has_vision = "vision" in self.model.lower() or "llava" in self.model.lower()
        return ProviderCapabilities(
            has_vision=has_vision,
            supports_tools=True,
            max_context_tokens=32000,
            cost_per_1k_input=0.0,
            cost_per_1k_output=0.0
        )

    async def chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        image_bytes: Optional[bytes] = None
    ) -> ModelResponse:
        ollama_msgs = []
        for m in messages:
            msg_obj = {"role": m["role"], "content": m["content"]}
            if m == messages[-1] and image_bytes:
                b64_img = base64.b64encode(image_bytes).decode("utf-8")
                msg_obj["images"] = [b64_img]
            ollama_msgs.append(msg_obj)

        payload = {
            "model": self.model,
            "messages": ollama_msgs,
            "stream": False
        }

        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(f"{self.base_url}/api/chat", json=payload)
            resp.raise_for_status()
            data = resp.json()

        content = data.get("message", {}).get("content", "")
        tin = data.get("prompt_eval_count", 0)
        tout = data.get("eval_count", 0)

        return ModelResponse(
            content=content,
            tokens_in=tin,
            tokens_out=tout,
            estimated_cost=0.0
        )
