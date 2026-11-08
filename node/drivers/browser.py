"""A Playwright-controlled Chromium tab used for browser-only runs.

It implements the same :class:`Computer` surface as the desktop driver, so the
agent loop and toolbox work unchanged; coordinates are viewport pixels.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from . import apps as app_catalog
from shared.computer import Computer, ComputerError, Frame

_MODIFIERS = {
    "ctrl": "Control",
    "control": "Control",
    "alt": "Alt",
    "option": "Alt",
    "shift": "Shift",
    "cmd": "Meta",
    "command": "Meta",
    "meta": "Meta",
    "win": "Meta",
    "super": "Meta",
}
_KEYS = {
    "esc": "Escape",
    "escape": "Escape",
    "return": "Enter",
    "enter": "Enter",
    "del": "Delete",
    "delete": "Delete",
    "ins": "Insert",
    "space": "Space",
    "tab": "Tab",
    "backspace": "Backspace",
    "up": "ArrowUp",
    "down": "ArrowDown",
    "left": "ArrowLeft",
    "right": "ArrowRight",
    "home": "Home",
    "end": "End",
    "pgup": "PageUp",
    "pgdn": "PageDown",
}

_LAUNCH_ARGS = [
    "--no-sandbox",
    "--disable-setuid-sandbox",
    "--disable-dev-shm-usage",
    "--disable-gpu",
    "--no-first-run",
    "--disable-background-networking",
]


def normalise_address(text: str) -> str:
    """Turn model/user input into a navigable URL (searching when it is not an address)."""
    value = (text or "").strip()
    if not value:
        raise ComputerError("No address supplied.")
    if app_catalog.is_url(value):
        return app_catalog.with_scheme(value)
    return app_catalog.search_url(value)


def _playwright_key(keys: str) -> str:
    parts: List[str] = []
    for part in (keys or "").split("+"):
        part = part.strip()
        if not part:
            continue
        low = part.lower()
        if low in _MODIFIERS:
            parts.append(_MODIFIERS[low])
        elif low in _KEYS:
            parts.append(_KEYS[low])
        else:
            parts.append(part if len(part) > 1 else part.lower())
    return "+".join(parts)


class BrowserComputer(Computer):
    """Drives one Chromium tab (headed when a display is available)."""

    backend = "browser"
    supports_real_input = False

    def __init__(
        self,
        viewport_width: int = 1280,
        viewport_height: int = 720,
        max_width: int = 1280,
        headless: Optional[bool] = None,
        user_data_dir: Optional[str] = None,
    ) -> None:
        self.viewport_width = viewport_width
        self.viewport_height = viewport_height
        self.max_width = max_width
        self.headless = headless
        self.user_data_dir = user_data_dir
        self._playwright: Any = None
        self._browser: Any = None
        self._context: Any = None
        self._page: Any = None

    @staticmethod
    def available() -> tuple[bool, str]:
        try:
            import playwright  # noqa: F401
        except Exception as exc:  # pragma: no cover - depends on install
            return False, f"Playwright is not installed: {exc}"
        return True, "A Chromium tab is controlled by Playwright."

    async def _ensure(self):
        if self._page is not None and not self._page.is_closed():
            return self._page
        try:
            from playwright.async_api import async_playwright
        except Exception as exc:  # pragma: no cover - depends on install
            raise ComputerError(f"Playwright is not available: {exc}") from exc

        headless = self.headless
        if headless is None:
            headless = not bool(os.environ.get("DISPLAY"))

        try:
            self._playwright = await async_playwright().start()
            viewport = {"width": self.viewport_width, "height": self.viewport_height}
            if self.user_data_dir:
                self._context = await self._playwright.chromium.launch_persistent_context(
                    self.user_data_dir,
                    headless=headless,
                    args=_LAUNCH_ARGS,
                    viewport=viewport,
                )
                self._page = (
                    self._context.pages[0]
                    if self._context.pages
                    else await self._context.new_page()
                )
            else:
                self._browser = await self._playwright.chromium.launch(
                    headless=headless, args=_LAUNCH_ARGS
                )
                self._context = await self._browser.new_context(viewport=viewport)
                self._page = await self._context.new_page()
            self._page.set_default_timeout(30_000)
        except Exception as exc:
            await self.close()
            raise ComputerError(f"Could not start the browser tab: {exc}") from exc
        return self._page

    async def _run(self, func, *args: Any, **kwargs: Any):
        try:
            return await func(*args, **kwargs)
        except ComputerError:
            raise
        except Exception as exc:
            raise ComputerError(str(exc)) from exc

    # -- observation ------------------------------------------------------
    async def capture(self) -> Frame:
        page = await self._ensure()

        async def _shot() -> bytes:
            return await page.screenshot(type="png")

        png = await self._run(_shot)
        width = int(page.viewport_size.get("width") if page.viewport_size else self.viewport_width)
        height = int(page.viewport_size.get("height") if page.viewport_size else self.viewport_height)
        try:
            title = await page.title()
        except Exception:
            title = ""
        label = f"Browser tab · {title or page.url}"
        return Frame(
            png=png,
            width=width,
            height=height,
            screen_width=width,
            screen_height=height,
            label=label,
        )

    # -- navigation -------------------------------------------------------
    async def navigate(self, url: str) -> None:
        address = normalise_address(url)
        page = await self._ensure()
        await self._run(page.goto, address, wait_until="domcontentloaded")

    # -- input ------------------------------------------------------------
    async def click(self, x: float, y: float, button: str = "left", clicks: int = 1) -> None:
        page = await self._ensure()
        await self._run(
            page.mouse.click,
            x,
            y,
            button=button,
            click_count=max(1, min(3, int(clicks))),
        )

    async def move(self, x: float, y: float) -> None:
        page = await self._ensure()
        await self._run(page.mouse.move, x, y)

    async def drag(self, from_x: float, from_y: float, to_x: float, to_y: float) -> None:
        page = await self._ensure()
        await self._run(page.mouse.move, from_x, from_y)
        await self._run(page.mouse.down)
        await self._run(page.mouse.move, to_x, to_y, steps=12)
        await self._run(page.mouse.up)

    async def scroll(self, x: float, y: float, amount: int) -> None:
        page = await self._ensure()
        await self._run(page.mouse.move, x, y)
        await self._run(page.mouse.wheel, 0, int(amount))

    async def type_text(self, text: str) -> None:
        page = await self._ensure()
        await self._run(page.keyboard.insert_text, text)

    async def press_key(self, keys: str) -> None:
        page = await self._ensure()
        spec = _playwright_key(keys)
        if not spec:
            raise ComputerError("No keys supplied.")
        await self._run(page.keyboard.press, spec)

    # -- page access ------------------------------------------------------
    async def current_url(self) -> str:
        page = await self._ensure()
        return page.url

    async def page_title(self) -> str:
        page = await self._ensure()
        return await self._run(page.title)

    async def page_text(self, selector: str | None = None, max_chars: int = 20_000) -> str:
        """Readable text of the page body, or of one element."""
        page = await self._ensure()
        if selector:
            text = await self._run(page.locator(selector).first.inner_text, timeout=15_000)
        else:
            text = await self._run(page.inner_text, "body")
        return (text or "")[:max_chars]

    async def perform(self, action: str, **kwargs: Any) -> Dict[str, Any]:
        """Run one structured browser action and return its observation."""
        page = await self._ensure()
        if action == "back":
            await self._run(page.go_back)
        elif action == "forward":
            await self._run(page.go_forward)
        elif action == "reload":
            await self._run(page.reload)
        elif action == "current_url":
            return {"url": page.url}
        elif action == "title":
            return {"title": await self._run(page.title)}
        elif action == "click":
            await self._run(
                page.locator(str(kwargs.get("selector") or "")).first.click, timeout=15_000
            )
        elif action == "fill":
            await self._run(
                page.locator(str(kwargs.get("selector") or "")).first.fill,
                str(kwargs.get("text") or ""),
            )
        elif action == "press":
            await self._run(page.keyboard.press, _playwright_key(str(kwargs.get("keys") or "")))
        elif action == "find_text":
            count = await self._run(page.get_by_text(str(kwargs.get("query") or "")).count)
            return {"matches": int(count or 0)}
        else:
            raise ComputerError(f"Unsupported browser action '{action}'.")
        return {"action": action, "url": page.url}

    # -- lifecycle --------------------------------------------------------
    async def close(self) -> None:
        context, browser, playwright = self._context, self._browser, self._playwright
        self._page = None
        self._context = None
        self._browser = None
        self._playwright = None
        for target, method in ((context, "close"), (browser, "close"), (playwright, "stop")):
            if target is None:
                continue
            try:
                await getattr(target, method)()
            except Exception:
                pass

    def describe(self) -> Dict[str, object]:
        info = super().describe()
        info.update(
            {
                "name": "Browser tab",
                "platform": "Chromium via Playwright",
                "screen_width": self.viewport_width,
                "screen_height": self.viewport_height,
                "note": "Controls a single browser tab. No desktop apps are launched.",
            }
        )
        return info


__all__ = ["BrowserComputer"]
