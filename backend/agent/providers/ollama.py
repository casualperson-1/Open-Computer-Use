"""Local Ollama adapter. Runs fully offline; images work with vision models."""

from __future__ import annotations

import base64
import html
import json
import re
from typing import Any, Callable, Dict, List, Optional

import httpx

from .base import ChatMessage, ModelProvider, ModelTurn, ProviderError, ToolCall, ToolSpec

LIBRARY_SEARCH_URL = "https://ollama.com/search"
_LIBRARY_LINK_RE = re.compile(r'<a[^>]+href="/library/([^"?#/]+)"[^>]*>(.*?)</a>', re.S)
_PARAGRAPH_RE = re.compile(r"<p[^>]*>(.*?)</p>", re.S)
_TAG_RE = re.compile(r"<[^>]+>")

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


def parse_library_html(page_html: str, limit: int = 40) -> List[Dict[str, str]]:
    """Extract model names and blurbs from an ollama.com/library search page."""
    results: List[Dict[str, str]] = []
    seen: set[str] = set()
    for match in _LIBRARY_LINK_RE.finditer(page_html or ""):
        name = match.group(1).strip()
        if not name or name in seen:
            continue
        seen.add(name)
        description = ""
        paragraph = _PARAGRAPH_RE.search(match.group(2))
        if paragraph:
            description = html.unescape(
                " ".join(_TAG_RE.sub(" ", paragraph.group(1)).split())
            )
        results.append({"name": name, "description": description[:180]})
        if len(results) >= limit:
            break
    return results


async def search_library(query: str, limit: int = 40) -> List[Dict[str, str]]:
    """Search the public Ollama model library (ollama.com/search)."""
    query = (query or "").strip()
    if not query:
        return []
    headers = {"User-Agent": "Open-Computer-Use/0.2"}
    try:
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
            response = await client.get(
                LIBRARY_SEARCH_URL, params={"q": query}, headers=headers
            )
    except httpx.HTTPError as exc:
        raise ProviderError(f"Could not reach the Ollama library: {exc}") from exc
    if response.status_code >= 400:
        raise ProviderError(f"Ollama library error {response.status_code}.")
    return parse_library_html(response.text, limit)


async def pull_model(
    base_url: str,
    model: str,
    on_progress: Optional[Callable[[Dict[str, Any]], None]] = None,
) -> None:
    """Pull a model through Ollama's streaming /api/pull endpoint."""
    base = (base_url or "http://localhost:11434").rstrip("/")
    timeout = httpx.Timeout(connect=10.0, read=3600.0, write=60.0, pool=10.0)
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream(
                "POST", f"{base}/api/pull", json={"model": model, "stream": True}
            ) as response:
                if response.status_code >= 400:
                    body = (await response.aread()).decode("utf-8", "replace")[:400]
                    raise ProviderError(
                        f"Ollama pull failed ({response.status_code}): {body}"
                    )
                async for line in response.aiter_lines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        update = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(update, dict):
                        continue
                    if update.get("error"):
                        raise ProviderError(f"Ollama pull failed: {update['error']}")
                    if on_progress:
                        on_progress(update)
                    if update.get("status") == "success":
                        return
    except httpx.HTTPError as exc:
        raise ProviderError(f"Could not reach Ollama: {exc}") from exc
