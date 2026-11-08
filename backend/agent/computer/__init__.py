"""Computer drivers for Open Computr Use."""

from __future__ import annotations

import logging
import platform
from typing import Optional

from .actions import ComputerToolbox
from .base import Computer, ComputerError, EmergencyStop, Frame
from .desktop import DesktopComputer
from .simulated import SimulatedComputer

logger = logging.getLogger(__name__)


def computer_availability() -> dict:
    ok, reason = DesktopComputer.available()
    return {
        "desktop_available": ok,
        "reason": reason,
        "platform": platform.platform(),
        "real_input": ok,
    }


def create_computer(
    backend: Optional[str] = None,
    max_width: int = 1280,
    human_mouse: bool = True,
) -> Computer:
    """Create the configured driver, falling back to the simulated desktop."""
    choice = (backend or "desktop").strip().lower()
    if choice in ("simulated", "sim", "dry-run", "demo", "virtual"):
        return SimulatedComputer(max_width=max_width)

    ok, reason = DesktopComputer.available()
    if ok:
        return DesktopComputer(max_width=max_width, human_mouse=human_mouse)

    logger.warning("Desktop control unavailable (%s). Falling back to simulated desktop.", reason)
    return SimulatedComputer(max_width=max_width)


__all__ = [
    "Computer",
    "ComputerError",
    "ComputerToolbox",
    "DesktopComputer",
    "EmergencyStop",
    "Frame",
    "SimulatedComputer",
    "computer_availability",
    "create_computer",
]
