from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional

@dataclass
class ProviderCapabilities:
    has_vision: bool = True
    supports_tools: bool = True
    max_context_tokens: int = 128000
    cost_per_1k_input: float = 0.0
    cost_per_1k_output: float = 0.0

@dataclass
class ModelResponse:
    content: str
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)
    tokens_in: int = 0
    tokens_out: int = 0
    estimated_cost: float = 0.0

class ModelProvider(ABC):
    @abstractmethod
    def get_capabilities(self) -> ProviderCapabilities:
        pass

    @abstractmethod
    async def chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        image_bytes: Optional[bytes] = None
    ) -> ModelResponse:
        pass
