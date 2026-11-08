"""Computer drivers the server can use.

Real mouse/keyboard/browser control runs on a desktop node, never in the server
process. The server therefore has exactly two options: the node's
:class:`~nodes.remote.RemoteComputer` when one is connected, and the safe
simulated desktop otherwise.
"""

from __future__ import annotations

import logging
import platform
from typing import Optional

from shared.computer import Computer, ComputerError, EmergencyStop, Frame

from .actions import ComputerToolbox
from .simulated import SimulatedComputer

logger = logging.getLogger(__name__)

_SIMULATED_ALIASES = {"simulated", "sim", "dry-run", "demo", "virtual"}


def computer_availability(node: Optional[dict] = None) -> dict:
    """Describe what the UI can drive right now.

    ``node`` is the connected node's summary (see ``nodes.hub.summary``), so the
    dashboard no longer probes the server's own display.
    """
    info = dict(node or {})
    connected = bool(info.get("connected"))
    backend = str(info.get("backend") or "")
    label = info.get("name") or info.get("node_id") or "a node"
    reason = (
        f"Node “{label}” is connected."
        if connected
        else "No desktop node is connected. Start the node agent on the machine you want to control."
    )
    return {
        "desktop_available": connected and backend != "browser",
        "reason": reason,
        "browser_available": connected and backend == "browser",
        "browser_reason": reason,
        "platform": str(info.get("platform") or platform.platform()),
        "real_input": connected,
        "node": info,
    }


def create_computer(
    backend: Optional[str] = None,
    max_width: int = 1280,
    human_mouse: bool = True,
    browser_profile_dir: Optional[str] = None,
    node: Optional[Computer] = None,
) -> Computer:
    """Return the connected node's driver, or the simulated desktop.

    ``node`` is a ready :class:`shared.computer.Computer` (normally a
    ``RemoteComputer`` bound to the connected node). Passing one keeps the
    desktop and browser modes working unchanged from the agent's point of view.
    """
    choice = (backend or "desktop").strip().lower()
    if choice in _SIMULATED_ALIASES:
        return SimulatedComputer(max_width=max_width)
    if node is not None:
        return node
    logger.warning(
        "No desktop node is connected; falling back to the simulated desktop for %s mode.",
        choice,
    )
    return SimulatedComputer(max_width=max_width)


__all__ = [
    "Computer",
    "ComputerError",
    "ComputerToolbox",
    "EmergencyStop",
    "Frame",
    "SimulatedComputer",
    "computer_availability",
    "create_computer",
]
