import json
from typing import List, Dict, Any, Optional
from .base import ModelProvider, ProviderCapabilities, ModelResponse

class MockProvider(ModelProvider):
    """Deterministic simulated model provider for automated integration testing and limits verification."""
    def __init__(self, fail_at_step: Optional[int] = None, simulated_cost_per_step: float = 0.05):
        self.step_call_count = 0
        self.fail_at_step = fail_at_step
        self.simulated_cost = simulated_cost_per_step

    def get_capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            has_vision=True,
            supports_tools=True,
            max_context_tokens=100000,
            cost_per_1k_input=0.001,
            cost_per_1k_output=0.002
        )

    async def chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        image_bytes: Optional[bytes] = None
    ) -> ModelResponse:
        self.step_call_count += 1
        last_msg = messages[-1]["content"]

        if self.step_call_count == 1:
            # 1. Accept cookies
            action = {
                "thought": "I notice a cookie banner covering the bottom. Let me accept it first.",
                "action": "click",
                "element_id": 1
            }
        elif self.step_call_count == 2:
            # 2. Type search query
            action = {
                "thought": "Now I search for 'wireless mouse' in the product search bar.",
                "action": "type",
                "element_id": 1,
                "text": "wireless mouse"
            }
        elif self.step_call_count == 3:
            # 3. Click search button
            action = {
                "thought": "Submitting the search query.",
                "action": "click",
                "element_id": 2
            }
        elif self.step_call_count == 4:
            # 4. Click the cheapest mouse deal button
            action = {
                "thought": "I see the Ultra Budget Wireless Mouse is only $14.50. Let's inspect the deal.",
                "action": "click",
                "element_id": 7
            }
        else:
            # 5. Conclude task with extracted deal
            action = {
                "thought": "Verified deal details. The cheapest wireless mouse is Ultra Budget Wireless Mouse at $14.50.",
                "action": "done",
                "summary": "Found cheapest wireless mouse for $14.50",
                "results": {
                    "product": "Ultra Budget Wireless Mouse",
                    "price": 14.50,
                    "original_price": 29.00,
                    "discount_pct": 50.0,
                    "seller": "TechWarehouse USA"
                }
            }

        return ModelResponse(
            content=json.dumps(action),
            tokens_in=500,
            tokens_out=150,
            estimated_cost=self.simulated_cost
        )
