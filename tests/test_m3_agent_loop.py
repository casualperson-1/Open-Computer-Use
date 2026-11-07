import asyncio
from pathlib import Path
from agent.browser.session import BrowserSession
from agent.perception.extractor import PerceptionEngine
from agent.actions.resolver import ActionResolver
from agent.actions.schema import Action
from agent.providers.mock import MockProvider
from agent.loop import AgentLoop

MOCK_SHOP_HTML = Path("/app/mock-shop/index.html")
if not MOCK_SHOP_HTML.exists():
    MOCK_SHOP_HTML = (Path(__file__).parent.parent / "mock-shop" / "index.html").resolve()

async def test_end_to_end_agent_run():
    print("Testing M3 End-to-End Agent Loop on Mock Shop...")
    session = BrowserSession(headless=False, fast_mode=True)
    await session.start()
    perception = PerceptionEngine(draw_som=False)
    resolver = ActionResolver(session, perception)
    
    # 1. Load initial shop page
    await resolver.execute(Action(name="goto", url=f"file://{MOCK_SHOP_HTML}"))
    await asyncio.sleep(0.5)

    provider = MockProvider()
    agent = AgentLoop(provider=provider, resolver=resolver, perception=perception, max_steps=10, max_cost=1.0)

    result = await agent.run(goal="find the cheapest wireless mouse on the mock shop")
    print("Agent Run Result:", result)
    assert result["status"] == "completed", f"Agent run did not complete: {result}"
    assert result["results"]["price"] == 14.50, f"Unexpected deal price: {result}"
    print("✔ End-to-end task completed successfully!")
    await session.close()

async def test_agent_cost_limit_stop():
    print("\nTesting M3 Hard Cost Limit Enforcement...")
    session = BrowserSession(headless=False, fast_mode=True)
    await session.start()
    perception = PerceptionEngine(draw_som=False)
    resolver = ActionResolver(session, perception)
    await resolver.execute(Action(name="goto", url=f"file://{MOCK_SHOP_HTML}"))

    # Set cost per step to 0.15 and budget limit to 0.20 -> must stop on step 2
    provider = MockProvider(simulated_cost_per_step=0.15)
    agent = AgentLoop(provider=provider, resolver=resolver, perception=perception, max_steps=10, max_cost=0.20)

    result = await agent.run(goal="find deals within small budget")
    print("Budget Exceeded Result:", result)
    assert result["status"] == "limit_reached", f"Expected limit_reached but got: {result}"
    print("✔ Budget limits enforced gracefully!")
    await session.close()

async def main():
    await test_end_to_end_agent_run()
    await test_agent_cost_limit_stop()
    print("\n✔ All M3 acceptance tests passed!")

if __name__ == "__main__":
    asyncio.run(main())
