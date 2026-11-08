"""Deterministic providers for tests, demos and the original browser loop."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Sequence, Union

from .base import (
    ChatMessage,
    ModelProvider,
    ModelResponse,
    ModelTurn,
    ProviderCapabilities,
    ToolCall,
    ToolSpec,
)


class MockProvider(ModelProvider):
    """Plays a fixed scripted shopping run (used by the browser acceptance tests)."""

    name = "mock"
    label = "Mock (offline)"
    default_model = "mock"
    has_vision = True
    requires_key = False

    def __init__(
        self,
        fail_at_step: Optional[int] = None,
        simulated_cost_per_step: float = 0.05,
        **kwargs: Any,
    ) -> None:
        super().__init__(model="mock", **kwargs)
        self.step_call_count = 0
        self.fail_at_step = fail_at_step
        self.simulated_cost = simulated_cost_per_step

    def get_capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            has_vision=True,
            supports_tools=True,
            max_context_tokens=100000,
            cost_per_1k_input=0.001,
            cost_per_1k_output=0.002,
        )

    def _next_action(self) -> Dict[str, Any]:
        if self.step_call_count == 1:
            return {
                "thought": "I notice a cookie banner covering the bottom. Let me accept it first.",
                "action": "click",
                "element_id": 1,
            }
        if self.step_call_count == 2:
            return {
                "thought": "Now I search for 'wireless mouse' in the product search bar.",
                "action": "type",
                "element_id": 1,
                "text": "wireless mouse",
            }
        if self.step_call_count == 3:
            return {
                "thought": "Submitting the search query.",
                "action": "click",
                "element_id": 2,
            }
        if self.step_call_count == 4:
            return {
                "thought": "I see the Ultra Budget Wireless Mouse is only $14.50. Let's inspect the deal.",
                "action": "click",
                "element_id": 7,
            }
        return {
            "thought": "Verified deal details. The cheapest wireless mouse is Ultra Budget Wireless Mouse at $14.50.",
            "action": "done",
            "summary": "Found cheapest wireless mouse for $14.50",
            "results": {
                "product": "Ultra Budget Wireless Mouse",
                "price": 14.50,
                "original_price": 29.00,
                "discount_pct": 50.0,
                "seller": "TechWarehouse USA",
            },
        }

    async def generate(
        self,
        messages: List[ChatMessage],
        tools: Optional[List[ToolSpec]] = None,
    ) -> ModelTurn:
        self.step_call_count += 1
        action = self._next_action()
        return ModelTurn(
            text=json.dumps(action),
            tokens_in=500,
            tokens_out=150,
            estimated_cost=self.simulated_cost,
        )

    async def chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        image_bytes: Optional[bytes] = None,
    ) -> ModelResponse:
        turn = await self.generate([])
        return ModelResponse(
            content=turn.text,
            tokens_in=turn.tokens_in,
            tokens_out=turn.tokens_out,
            estimated_cost=turn.estimated_cost,
        )

    async def list_models(self) -> List[str]:
        return ["mock"]


ScriptItem = Union[ModelTurn, Dict[str, Any]]


class ScriptedProvider(ModelProvider):
    """Returns a pre-built list of turns; records every request it receives."""

    name = "scripted"
    label = "Scripted (tests)"
    default_model = "scripted"
    has_vision = True
    requires_key = False

    def __init__(self, script: Sequence[ScriptItem], **kwargs: Any) -> None:
        super().__init__(model="scripted", **kwargs)
        self.script: List[ModelTurn] = [self._coerce(item) for item in script]
        self.requests: List[List[ChatMessage]] = []
        self.request_tools: List[Optional[List[ToolSpec]]] = []

    def _coerce(self, item: ScriptItem) -> ModelTurn:
        if isinstance(item, ModelTurn):
            return item
        calls = []
        for call in item.get("tool_calls", []):
            if isinstance(call, ToolCall):
                calls.append(call)
            elif isinstance(call, (tuple, list)) and len(call) == 2:
                calls.append(ToolCall(id=f"call_{len(calls)}", name=str(call[0]), arguments=dict(call[1])))
            elif isinstance(call, dict):
                calls.append(
                    ToolCall(
                        id=str(call.get("id") or f"call_{len(calls)}"),
                        name=str(call.get("name") or ""),
                        arguments=dict(call.get("arguments") or {}),
                    )
                )
        return ModelTurn(
            text=str(item.get("text") or ""),
            tool_calls=calls,
            tokens_in=int(item.get("tokens_in") or 10),
            tokens_out=int(item.get("tokens_out") or 5),
            estimated_cost=float(item.get("estimated_cost") or 0.0),
        )

    async def generate(
        self,
        messages: List[ChatMessage],
        tools: Optional[List[ToolSpec]] = None,
    ) -> ModelTurn:
        self.requests.append(list(messages))
        self.request_tools.append(tools)
        if not self.script:
            return ModelTurn(text="Script exhausted.")
        if len(self.script) == 1:
            return self.script[0]
        return self.script.pop(0)

    async def list_models(self) -> List[str]:
        return ["scripted"]
