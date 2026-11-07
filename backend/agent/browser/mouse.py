import asyncio
import os
import random
import subprocess
from pathlib import Path
from typing import Optional, Tuple
from playwright.async_api import Page
from .bezier import generate_bezier_path

CURSOR_JS_PATH = Path(__file__).parent / "cursor.js"

class MouseController:
    def __init__(self, page: Page, fast_mode: bool = False, real_mouse: bool = False):
        self.page = page
        self.fast_mode = fast_mode
        self.real_mouse = real_mouse
        self.current_pos: Tuple[float, float] = (100.0, 100.0)
        self._cursor_js = CURSOR_JS_PATH.read_text(encoding="utf-8")

    async def inject_cursor(self):
        """Inject synthetic visual cursor overlay into current page."""
        try:
            await self.page.add_init_script(self._cursor_js)
            await self.page.evaluate(self._cursor_js)
        except Exception:
            pass

    async def move_to(self, target_x: float, target_y: float, duration_ms: Optional[float] = None):
        """Move mouse cursor smoothly along a natural Bezier curve to target coordinates."""
        if self.real_mouse:
            # OS level xdotool movement if real_mouse is enabled
            await self._move_real_mouse(target_x, target_y)
            self.current_pos = (target_x, target_y)
            return

        if self.fast_mode:
            await self.page.mouse.move(target_x, target_y)
            self.current_pos = (target_x, target_y)
            return

        dist = ((target_x - self.current_pos[0])**2 + (target_y - self.current_pos[1])**2)**0.5
        # Calculate dynamic duration (300-900ms) based on distance
        if duration_ms is None:
            duration_ms = min(900.0, max(300.0, dist * 0.85 + random.uniform(50, 150)))

        steps = max(15, int(duration_ms / 20))
        path = generate_bezier_path(self.current_pos, (target_x, target_y), steps=steps)
        interval = (duration_ms / 1000.0) / len(path)

        for pt in path:
            await self.page.mouse.move(pt[0], pt[1])
            await asyncio.sleep(interval)

        self.current_pos = (target_x, target_y)

    async def _move_real_mouse(self, x: float, y: float):
        display = os.environ.get("DISPLAY", ":99")
        try:
            proc = await asyncio.create_subprocess_exec(
                "xdotool", "mousemove", str(int(x)), str(int(y)),
                env={**os.environ, "DISPLAY": display}
            )
            await proc.wait()
        except Exception:
            pass
        # Always synchronize synthetic mouse coordinates
        await self.page.mouse.move(x, y)

    async def click(self, x: Optional[float] = None, y: Optional[float] = None, button: str = "left"):
        """Move (if coordinates provided), pause briefly, then click."""
        if x is not None and y is not None:
            await self.move_to(x, y)

        if not self.fast_mode:
            # Short natural hesitation before clicking (40-100ms)
            await asyncio.sleep(random.uniform(0.04, 0.10))

        if self.real_mouse:
            display = os.environ.get("DISPLAY", ":99")
            btn_num = "1" if button == "left" else "3" if button == "right" else "2"
            try:
                proc = await asyncio.create_subprocess_exec(
                    "xdotool", "click", "--window", "0", btn_num,
                    env={**os.environ, "DISPLAY": display}
                )
                await proc.wait()
            except Exception:
                pass

        # Dispatch Playwright mouse click for reliable web page focus & DOM events
        await self.page.mouse.down(button=button)
        await asyncio.sleep(random.uniform(0.04, 0.08))
        await self.page.mouse.up(button=button)

    async def double_click(self, x: Optional[float] = None, y: Optional[float] = None):
        """Double click at given coordinates."""
        await self.click(x, y)
        await asyncio.sleep(random.uniform(0.08, 0.15))
        await self.click()

    async def right_click(self, x: Optional[float] = None, y: Optional[float] = None):
        """Right click at given coordinates."""
        await self.click(x, y, button="right")

    async def hover(self, x: float, y: float):
        """Hover over specific coordinates."""
        await self.move_to(x, y)

    async def drag(self, start_x: float, start_y: float, end_x: float, end_y: float):
        """Click and drag from start to end position."""
        await self.move_to(start_x, start_y)
        await asyncio.sleep(0.08)
        await self.page.mouse.down()
        await asyncio.sleep(0.05)
        await self.move_to(end_x, end_y, duration_ms=600)
        await asyncio.sleep(0.08)
        await self.page.mouse.up()

    async def smooth_scroll(self, delta_y: float, steps: int = 12):
        """Perform smooth stepped vertical scroll instead of instant jump."""
        if self.fast_mode:
            await self.page.mouse.wheel(0, delta_y)
            return

        step_amount = delta_y / steps
        for _ in range(steps):
            await self.page.mouse.wheel(0, step_amount)
            await asyncio.sleep(0.035)

    async def type_text(self, text: str, delay_range: Tuple[float, float] = (0.04, 0.12)):
        """Type text with variable human-like per-keystroke delays."""
        if self.fast_mode:
            await self.page.keyboard.type(text)
            return

        for char in text:
            await self.page.keyboard.type(char)
            await asyncio.sleep(random.uniform(*delay_range))
