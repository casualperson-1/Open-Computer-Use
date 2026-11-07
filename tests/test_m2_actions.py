import asyncio
from pathlib import Path
from agent.browser.session import BrowserSession
from agent.perception.extractor import PerceptionEngine
from agent.actions.resolver import ActionResolver
from agent.actions.schema import Action

MOCK_SHOP_HTML = Path("/app/mock-shop/index.html")
if not MOCK_SHOP_HTML.exists():
    MOCK_SHOP_HTML = (Path(__file__).parent.parent / "mock-shop" / "index.html").resolve()

async def run_m2_acceptance_test():
    print(f"Loading Mock Shop: file://{MOCK_SHOP_HTML}")
    session = BrowserSession(headless=False, fast_mode=False)
    await session.start()
    perception = PerceptionEngine(draw_som=True)
    resolver = ActionResolver(session, perception)

    try:
        # Step 1: Navigate to shop
        res = await resolver.execute(Action(name="goto", url=f"file://{MOCK_SHOP_HTML}"))
        assert res["success"], f"Goto failed: {res}"
        await asyncio.sleep(0.5)

        # Step 2: Extract interactive elements
        state = await perception.capture(session.page, vision_mode=True)
        print(f"Captured {len(state.elements)} interactive elements on page")
        assert len(state.elements) >= 5, "Expected interactive elements not detected"

        # Step 3: Accept cookies banner
        cookie_el = next((el for el in state.elements if "Accept Cookies" in el.text), None)
        assert cookie_el is not None, "Accept Cookies button not found"
        print(f"Clicking cookie banner (id={cookie_el.id})...")
        res = await resolver.execute(Action(name="click", element_id=cookie_el.id))
        assert res["success"]

        # Step 4: Search for "mouse"
        state = await perception.capture(session.page, vision_mode=False)
        input_el = next((el for el in state.elements if el.is_input and "Search" in el.text or el.tag == "input"), None)
        assert input_el is not None, "Search input element not found"
        print(f"Typing query 'mouse' into search field (id={input_el.id})...")
        await resolver.execute(Action(name="type", element_id=input_el.id, text="mouse"))

        search_btn = next((el for el in state.elements if el.text == "Search"), None)
        assert search_btn is not None, "Search button not found"
        await resolver.execute(Action(name="click", element_id=search_btn.id))
        await asyncio.sleep(0.5)

        # Step 5: Smooth scroll down to view products
        print("Scrolling down to inspect deals...")
        await resolver.execute(Action(name="scroll", direction="down", amount=350))
        await asyncio.sleep(0.4)

        # Step 6: Click view deal on cheapest mouse
        state = await perception.capture(session.page, vision_mode=False)
        deal_btns = [el for el in state.elements if "View Deal" in el.text]
        assert len(deal_btns) > 0, "No 'View Deal' buttons found"
        print(f"Clicking deal button (id={deal_btns[1].id})...")
        await resolver.execute(Action(name="click", element_id=deal_btns[1].id))
        await asyncio.sleep(0.8)

        # Verify deal confirmation modal appears
        modal_text = await session.page.inner_text("#deal-details-modal")
        print(f"Modal content verified:\n{modal_text}")
        assert "Deal Verified" in modal_text, "Deal verification failed"
        print("\n✔ M2 Acceptance Test: Scripted action sequence completed multi-page flow on mock shop successfully!")

    finally:
        await session.close()

if __name__ == "__main__":
    asyncio.run(run_m2_acceptance_test())
