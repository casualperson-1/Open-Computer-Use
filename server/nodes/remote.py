"""A :class:`shared.computer.Computer` that forwards actions to a node."""

from __future__ import annotations

import base64
from typing import Any, Dict, Optional

from shared.computer import Computer, ComputerError, Frame
from shared.protocol import DEFAULT_TIMEOUT_MS

from .hub import NodeCommandError, NodeConnection, NodeHub, NodeTimeout, NodeUnavailable


class RemoteComputer(Computer):
    """Drives the desktop of one connected node over the hub."""

    supports_real_input = True

    def __init__(
        self,
        hub: NodeHub,
        node_id: Optional[str] = None,
        *,
        max_width: int = 1280,
        timeout: float = DEFAULT_TIMEOUT_MS / 1000.0,
    ) -> None:
        self.hub = hub
        self.node_id = node_id
        self.max_width = max_width
        self.timeout = timeout
        connection = hub.connection(node_id)
        self.backend = connection.backend if connection is not None else "node"

    # -- plumbing --------------------------------------------------------
    @property
    def connection(self) -> Optional[NodeConnection]:
        return self.hub.connection(self.node_id)

    async def _call(self, name: str, params: Dict[str, Any], timeout: Optional[float] = None):
        try:
            return await self.hub.dispatch(
                name, params, timeout=timeout or self.timeout, node_id=self.node_id
            )
        except (NodeUnavailable, NodeTimeout, NodeCommandError) as exc:
            raise ComputerError(str(exc)) from exc

    # -- observation -----------------------------------------------------
    async def capture(self) -> Frame:
        data = await self._call("screenshot", {}, timeout=max(self.timeout, 30.0))
        try:
            png = base64.b64decode(data["png_base64"])
        except (KeyError, ValueError) as exc:
            raise ComputerError("The node sent an unreadable screenshot.") from exc
        cursor = data.get("cursor") or [0, 0]
        return Frame(
            png=png,
            width=int(data.get("width") or 0),
            height=int(data.get("height") or 0),
            screen_width=int(data.get("screen_width") or 0),
            screen_height=int(data.get("screen_height") or 0),
            cursor_x=float(cursor[0]),
            cursor_y=float(cursor[1]),
            label=str(data.get("label") or ""),
        )

    # -- input -----------------------------------------------------------
    async def move(self, x: float, y: float) -> None:
        await self._call("mouse_move", {"x": x, "y": y})

    async def click(self, x: float, y: float, button: str = "left", clicks: int = 1) -> None:
        await self._call("click", {"x": x, "y": y, "button": button, "clicks": int(clicks)})

    async def drag(self, from_x: float, from_y: float, to_x: float, to_y: float) -> None:
        await self._call(
            "drag", {"from_x": from_x, "from_y": from_y, "to_x": to_x, "to_y": to_y}
        )

    async def scroll(self, x: float, y: float, amount: int) -> None:
        await self._call("scroll", {"x": x, "y": y, "amount": int(amount)})

    async def type_text(self, text: str) -> None:
        await self._call("type", {"text": text})

    async def press_key(self, keys: str) -> None:
        await self._call("key_combo", {"keys": keys})

    # -- browser ---------------------------------------------------------
    async def navigate(self, url: str) -> None:
        await self._call("open_url", {"url": url}, timeout=max(self.timeout, 60.0))

    async def browser_action(self, action: str, **kwargs: Any) -> Dict[str, Any]:
        params = {"action": action, **{key: value for key, value in kwargs.items() if value is not None}}
        return await self._call("browser_action", params, timeout=max(self.timeout, 60.0))

    async def page_text(self, selector: Optional[str] = None, max_chars: int = 20_000) -> str:
        params: Dict[str, Any] = {"max_chars": max_chars}
        if selector:
            params["selector"] = selector
        data = await self._call("get_page_text", params, timeout=max(self.timeout, 60.0))
        return str(data.get("text") or "")

    # -- apps ------------------------------------------------------------
    async def list_apps(self, query: str = "") -> Dict[str, Any]:
        return await self._call("list_apps", {"query": query} if query else {}, timeout=15.0)

    async def open_item(self, target: str, kind: Optional[str] = None) -> Dict[str, Any]:
        params: Dict[str, Any] = {"target": target}
        if kind:
            params["kind"] = kind
        return await self._call("open_item", params, timeout=max(self.timeout, 60.0))

    # -- lifecycle -------------------------------------------------------
    def describe(self) -> Dict[str, object]:
        connection = self.connection
        info: Dict[str, object] = {
            "backend": self.backend,
            "real_input": self.supports_real_input,
            "name": f"Node · {connection.name}" if connection else "Desktop node",
            "platform": connection.platform if connection else "",
            "screen_width": None,
            "screen_height": None,
            "note": "Controls a remote machine through its local node agent.",
            "node_id": self.node_id,
        }
        if connection is not None:
            screen = connection.screen or {}
            info["screen_width"] = screen.get("width")
            info["screen_height"] = screen.get("height")
        return info

    async def close(self) -> None:
        """Nothing to close: the node owns the driver and the socket."""
        return None


__all__ = ["RemoteComputer"]
