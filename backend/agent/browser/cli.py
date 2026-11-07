import asyncio
from pathlib import Path
import click
from rich.console import Console
from .session import BrowserSession

console = Console()
DEMO_HTML = (Path(__file__).parent / "demo_page.html").resolve()

@click.group()
def cli():
    """Agent Browser CLI"""
    pass

@cli.command()
@click.option("--url", default=None, help="Target URL (defaults to built-in acceptance test page)")
@click.option("--fast", is_flag=True, help="Disable animation delays")
@click.option("--real-mouse", is_flag=True, help="Use xdotool OS-level mouse control")
@click.option("--headless", is_flag=True, help="Run headless instead of headed")
def mouse_demo(url: str, fast: bool, real_mouse: bool, headless: bool):
    """Run the visible mouse demonstration and verification test."""
    asyncio.run(run_mouse_demo(url, fast, real_mouse, headless))

async def run_mouse_demo(url: str = None, fast: bool = False, real_mouse: bool = False, headless: bool = False):
    target_url = url if url else f"file://{DEMO_HTML}"
    console.print(f"[bold cyan]Starting Mouse Demo against:[/bold cyan] {target_url}")

    session = BrowserSession(headless=headless, fast_mode=fast, real_mouse=real_mouse)
    await session.start()

    try:
        console.print("[yellow]1. Navigating to page...[/yellow]")
        await session.page.goto(target_url)
        await asyncio.sleep(1.0)

        # 1. Click button
        console.print("[yellow]2. Locating button and moving mouse...[/yellow]")
        btn = await session.page.wait_for_selector("#test-button")
        box = await btn.bounding_box()
        if box:
            target_x = box["x"] + box["width"] / 2
            target_y = box["y"] + box["height"] / 2
            console.print(f"Moving to button at ({target_x:.1f}, {target_y:.1f}) along Bezier path...")
            await session.mouse.click(target_x, target_y)
            await asyncio.sleep(0.5)

            status = await session.page.inner_text("#click-status")
            console.print(f"[green]Button status: {status}[/green]")
            assert "Successfully" in status, f"Click failed, status: {status}"

        # 2. Type in input field
        console.print("[yellow]3. Moving to text field and typing...[/yellow]")
        inp = await session.page.wait_for_selector("#test-input")
        ibox = await inp.bounding_box()
        if ibox:
            ix = ibox["x"] + ibox["width"] / 4
            iy = ibox["y"] + ibox["height"] / 2
            await session.mouse.click(ix, iy)
            test_phrase = "Self-Hosted Agent"
            console.print(f"Typing '{test_phrase}' with realistic keystroke latency...")
            await session.mouse.type_text(test_phrase)
            await asyncio.sleep(0.5)

            t_status = await session.page.inner_text("#typing-status")
            console.print(f"[green]Typing status: {t_status}[/green]")
            assert test_phrase in t_status, f"Typing failed, got: {t_status}"

        # 3. Smooth scroll down
        console.print("[yellow]4. Performing smooth stepped scroll...[/yellow]")
        await session.mouse.smooth_scroll(600, steps=15)
        await asyncio.sleep(0.5)

        # 4. Click bottom button
        console.print("[yellow]5. Clicking bottom target after scroll...[/yellow]")
        bottom_btn = await session.page.wait_for_selector("#bottom-button")
        bbox = await bottom_btn.bounding_box()
        if bbox:
            bx = bbox["x"] + bbox["width"] / 2
            by = bbox["y"] + bbox["height"] / 2
            await session.mouse.click(bx, by)
            await asyncio.sleep(0.5)

            s_status = await session.page.inner_text("#scroll-status")
            console.print(f"[green]Scroll target status: {s_status}[/green]")
            assert "Clicked" in s_status, f"Bottom button click failed: {s_status}"

        console.print("[bold green]✔ All M1 mouse control acceptance assertions passed![/bold green]")
        await asyncio.sleep(1.0)
    finally:
        await session.close()

if __name__ == "__main__":
    cli()
