"""Desktop, browser and app drivers that run on the node.

Drivers are imported lazily: ``pyautogui`` can call ``sys.exit`` on import when
tkinter is missing, and the browser driver needs Playwright, so neither should
break ``node doctor`` or the control page.
"""

from __future__ import annotations

from typing import Optional

from shared.computer import Computer

__all__ = ["create_driver", "apps"]

_BROWSER_ALIASES = {"browser", "browser-tab", "web", "chromium", "chrome"}


def create_driver(
    kind: str = "desktop",
    *,
    max_width: int = 1280,
    human_mouse: bool = True,
    user_data_dir: Optional[str] = None,
    viewport_width: int = 1280,
    viewport_height: int = 720,
    headless: Optional[bool] = None,
) -> Computer:
    """Build the driver for ``kind`` (``desktop`` or ``browser``)."""
    choice = (kind or "desktop").strip().lower()
    if choice in _BROWSER_ALIASES:
        from .browser import BrowserComputer

        return BrowserComputer(
            viewport_width=viewport_width,
            viewport_height=viewport_height,
            max_width=max_width,
            headless=headless,
            user_data_dir=user_data_dir,
        )

    from .desktop import DesktopComputer

    return DesktopComputer(max_width=max_width, human_mouse=human_mouse)
