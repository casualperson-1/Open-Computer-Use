"""Provider-neutral chat/tool interface used by the Open Computer Use agent loop.

Every model backend serialises the same ``ChatMessage`` objects and normalised
``ToolSpec`` definitions, so the agent loop never needs provider-specific code.
"""

from __future__ import annotations

import base64
import ipaddress
import json
import logging
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)


class ProviderError(RuntimeError):
    """Raised when a provider cannot complete a request (network, auth, quota...)."""


_SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*://")
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0", "::1", "host.docker.internal"}


def _is_local_host(host: str) -> bool:
    if host in _LOCAL_HOSTS or host.endswith(".local") or host.endswith(".internal"):
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return address.is_loopback or address.is_private or address.is_link_local


def normalise_base_url(value: Optional[str]) -> str:
    """Return an absolute base URL, tolerating hosts saved without a scheme.

    A value like ``generativelanguage.googleapis.com/v1beta`` is a common
    configuration mistake and makes httpx fail with "Request URL is missing an
    'http://' or 'https://' protocol". Assume https for remote hosts and http
    for loopback/private endpoints.

    A scheme-less single label such as ``owner`` is not a hostname we can use
    (a typo or an account name, not an endpoint), so it returns ``""`` and the
    caller falls back to the provider's default. Explicit URLs are preserved.
    """
    text = (value or "").strip()
    if not text:
        return ""
    if text.startswith("//"):
        text = "https:" + text
    match = _SCHEME_RE.match(text)
    if match:
        if match.group(0)[:-3].lower() not in {"http", "https"}:
            return ""
    else:
        authority = text.split("/", 1)[0].strip()
        if authority.startswith("["):
            host = authority[1 : authority.find("]")] if "]" in authority else authority
        else:
            host = authority.split(":", 1)[0]
        host = host.lower()
        if "." not in host and not _is_local_host(host):
            return ""
        text = ("http://" if _is_local_host(host) else "https://") + text
    return text.rstrip("/")


# ---------------------------------------------------------------------------
# Normalised conversation model
# ---------------------------------------------------------------------------


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: Dict[str, Any] = field(default_factory=dict)

    def openai_dict(self) -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    def anthropic_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.parameters,
        }

    def gemini_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": _clean_gemini_schema(self.parameters),
        }


def _clean_gemini_schema(value: Any) -> Any:
    """Convert JSON-Schema to the subset accepted by the Gemini REST API."""
    if isinstance(value, dict):
        cleaned: Dict[str, Any] = {}
        for key, item in value.items():
            if key in ("additionalProperties", "$schema", "title", "default"):
                continue
            if key == "type" and isinstance(item, str):
                cleaned[key] = item.upper()
            elif key == "properties" and isinstance(item, dict):
                cleaned[key] = {name: _clean_gemini_schema(schema) for name, schema in item.items()}
            elif key == "items":
                cleaned[key] = _clean_gemini_schema(item)
            else:
                cleaned[key] = item
        return cleaned
    if isinstance(value, list):
        return [_clean_gemini_schema(item) for item in value]
    return value


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: Dict[str, Any] = field(default_factory=dict)
    # Gemini 3 requires the opaque signature returned on a functionCall part to
    # be echoed back with that call on the next turn.
    thought_signature: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {"id": self.id, "name": self.name, "arguments": self.arguments}


@dataclass
class ChatMessage:
    role: str  # system | user | assistant | tool
    content: str = ""
    images: List[bytes] = field(default_factory=list)
    tool_calls: List[ToolCall] = field(default_factory=list)
    tool_call_id: Optional[str] = None
    name: Optional[str] = None
    is_error: bool = False


@dataclass
class ModelTurn:
    text: str = ""
    tool_calls: List[ToolCall] = field(default_factory=list)
    tokens_in: int = 0
    tokens_out: int = 0
    estimated_cost: float = 0.0


@dataclass
class ProviderCapabilities:
    has_vision: bool = True
    supports_tools: bool = True
    max_context_tokens: int = 128000
    cost_per_1k_input: float = 0.0
    cost_per_1k_output: float = 0.0


@dataclass
class ModelResponse:
    """Legacy response type kept for the browser-agent modules."""

    content: str
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)
    tokens_in: int = 0
    tokens_out: int = 0
    estimated_cost: float = 0.0


def image_block_openai(image: bytes) -> Dict[str, Any]:
    encoded = base64.b64encode(image).decode("ascii")
    return {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{encoded}"}}


def image_block_anthropic(image: bytes) -> Dict[str, Any]:
    encoded = base64.b64encode(image).decode("ascii")
    return {
        "type": "image",
        "source": {"type": "base64", "media_type": "image/png", "data": encoded},
    }


# ---------------------------------------------------------------------------
# Provider base class
# ---------------------------------------------------------------------------


class ModelProvider(ABC):
    """Base class for all BYOK model adapters."""

    name: str = "base"
    label: str = "Base"
    default_model: str = ""
    default_base_url: str = ""
    has_vision: bool = True
    requires_key: bool = True
    context_tokens: int = 128000
    input_cost_per_1k: float = 0.0
    output_cost_per_1k: float = 0.0

    def __init__(
        self,
        model: Optional[str] = None,
        api_key: str = "",
        base_url: Optional[str] = None,
        max_tokens: int = 4096,
        temperature: float = 0.2,
        timeout: float = 180.0,
        **_: Any,
    ) -> None:
        self.model = (model or self.default_model).strip()
        self.api_key = (api_key or "").strip()
        configured = normalise_base_url(base_url)
        if base_url and base_url.strip() and not configured:
            logger.warning(
                "Ignoring unusable %s base URL %r; using the default endpoint",
                self.label,
                base_url,
            )
        self.base_url = configured or normalise_base_url(self.default_base_url)
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.timeout = timeout

    # -- metadata ---------------------------------------------------------
    def get_capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            has_vision=self.has_vision,
            supports_tools=True,
            max_context_tokens=self.context_tokens,
            cost_per_1k_input=self.input_cost_per_1k,
            cost_per_1k_output=self.output_cost_per_1k,
        )

    def metadata(self) -> Dict[str, Any]:
        return {
            "id": self.name,
            "label": self.label,
            "default_model": self.default_model,
            "needs_key": self.requires_key,
            "needs_base_url": self.name in ("openai-compatible", "ollama"),
            "has_vision": self.has_vision,
            "context_tokens": self.context_tokens,
            "input_cost_per_1k": self.input_cost_per_1k,
            "output_cost_per_1k": self.output_cost_per_1k,
        }

    # -- core API ---------------------------------------------------------
    @abstractmethod
    async def generate(
        self,
        messages: List[ChatMessage],
        tools: Optional[List[ToolSpec]] = None,
    ) -> ModelTurn:
        """Return the next assistant turn for the conversation."""

    async def list_models(self) -> List[str]:
        """List models available to this key. Optional for BYOK UIs."""
        raise ProviderError(f"{self.label} does not support model listing.")

    # -- helpers ----------------------------------------------------------
    async def _post_json(
        self,
        url: str,
        payload: Dict[str, Any],
        headers: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(url, json=payload, headers=headers or {})
        except httpx.HTTPError as exc:
            raise ProviderError(f"Could not reach {self.label} at {url}: {exc}") from exc

        if response.status_code >= 400:
            raise ProviderError(self._error_text(response))
        try:
            return response.json()
        except ValueError as exc:
            raise ProviderError(f"{self.label} returned a non-JSON response") from exc

    async def _get_json(
        self,
        url: str,
        headers: Optional[Dict[str, str]] = None,
        params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=min(self.timeout, 45.0)) as client:
                response = await client.get(url, headers=headers or {}, params=params or {})
        except httpx.HTTPError as exc:
            raise ProviderError(f"Could not reach {self.label} at {url}: {exc}") from exc

        if response.status_code >= 400:
            raise ProviderError(self._error_text(response))
        try:
            return response.json()
        except ValueError as exc:
            raise ProviderError(f"{self.label} returned a non-JSON response") from exc

    def _error_text(self, response: httpx.Response) -> str:
        detail = ""
        try:
            data = response.json()
            if isinstance(data, dict):
                error = data.get("error")
                if isinstance(error, dict):
                    detail = str(error.get("message") or error)
                elif error:
                    detail = str(error)
                detail = detail or str(data.get("message") or data.get("detail") or "")
        except ValueError:
            detail = response.text[:400]
        detail = detail.strip()[:600]
        return f"{self.label} API error {response.status_code}" + (f": {detail}" if detail else "")

    def _cost(self, tokens_in: int, tokens_out: int) -> float:
        return round(
            (tokens_in / 1000.0) * self.input_cost_per_1k
            + (tokens_out / 1000.0) * self.output_cost_per_1k,
            6,
        )

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        return max(1, len(text) // 4)

    # -- legacy compatibility --------------------------------------------
    async def chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        image_bytes: Optional[bytes] = None,
    ) -> ModelResponse:
        """Adapter for the original browser loop, which speaks plain dicts."""
        normalised: List[ChatMessage] = []
        for index, message in enumerate(messages):
            role = message.get("role", "user")
            images: List[bytes] = []
            if image_bytes and role == "user" and index == len(messages) - 1:
                images = [image_bytes]
            normalised.append(
                ChatMessage(role=role, content=str(message.get("content") or ""), images=images)
            )

        tool_specs: List[ToolSpec] = []
        for tool in tools or []:
            fn = tool.get("function", tool)
            if fn.get("name"):
                tool_specs.append(
                    ToolSpec(
                        name=fn["name"],
                        description=fn.get("description", ""),
                        parameters=fn.get("parameters", {"type": "object", "properties": {}}),
                    )
                )

        turn = await self.generate(normalised, tool_specs or None)
        return ModelResponse(
            content=turn.text,
            tool_calls=[
                {"id": call.id, "type": "function", "function": {"name": call.name, "arguments": json.dumps(call.arguments)}}
                for call in turn.tool_calls
            ],
            tokens_in=turn.tokens_in,
            tokens_out=turn.tokens_out,
            estimated_cost=turn.estimated_cost,
        )
