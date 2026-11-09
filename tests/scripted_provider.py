"""Scripted model provider for tests.

The old ``agent.providers.mock`` module was removed with the legacy browser
stack. This is the only piece those tests still need: a provider that returns
pre-built turns and records the requests the loop sent it.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Union

from agent.providers.base import (
    ChatMessage,
    ModelProvider,
    ModelTurn,
    ToolCall,
    ToolSpec,
)

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
                calls.append(
                    ToolCall(
                        id=f"call_{len(calls)}",
                        name=str(call[0]),
                        arguments=dict(call[1]),
                    )
                )
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
