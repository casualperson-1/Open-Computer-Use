"""Computer drivers for Open Computer Use."""

from __future__ import annotations

import logging
import platform
from typing import Optional

from .actions import ComputerToolbox
from .base import Computer, ComputerError, EmergencyStop, Frame
from .browser import BrowserComputer
from .desktop import DesktopComputer
from .simulated import SimulatedComputer

logger = logging.getLogger(__name__)


def computer_availability() -> dict:
    ok, reason = DesktopComputer.available()
    browser_ok, browser_reason = BrowserComputer.available()
    return {
        "desktop_available": ok,
        "reason": reason,
        "browser_available": browser_ok,
        "browser_reason": browser_reason,
        "platform": platform.platform(),
        "real_input": ok,
    }


def create_computer(
    backend: Optional[str] = None,
    max_width: int = 1280,
    human_mouse: bool = True,
    browser_profile_dir: Optional[str] = None,
) -> Computer:
    """Create the configured driver, falling back to the simulated desktop."""
    choice = (backend or "desktop").strip().lower()
    if choice in ("simulated", "sim", "dry-run", "demo", "virtual"):
        return SimulatedComputer(max_width=max_width)
    if choice in ("browser", "browser-tab", "web", "chromium", "chrome"):
        return BrowserComputer(
            max_width=max_width,
            user_data_dir=browser_profile_dir,
        )

    ok, reason = DesktopComputer.available()
    if ok:
        return DesktopComputer(max_width=max_width, human_mouse=human_mouse)

    logger.warning("Desktop control unavailable (%s). Falling back to simulated desktop.", reason)
    return SimulatedComputer(max_width=max_width)


__all__ = [
    "Computer",
    "ComputerError",
    "ComputerToolbox",
    "BrowserComputer",
    "DesktopComputer",
    "EmergencyStop",
    "Frame",
    "SimulatedComputer",
    "computer_availability",
    "create_computer",
]
