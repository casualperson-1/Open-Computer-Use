"""A :class:`shared.computer.Computer` that forwards actions to a node.

This is where the server-side policy is applied: every command is checked
against the allowlists, the per-run rate limit and (optionally) an approval
prompt before it is sent, and the outcome is written to the audit log. The node
enforces its own policy independently.
"""

from __future__ import annotations

import base64
from typing import Any, Awaitable, Callable, Dict, Optional

from shared.computer import Computer, ComputerError, Frame
from shared.policy import RateLimiter, describe_command
from shared.protocol import DEFAULT_TIMEOUT_MS

from security.policy import ServerNodePolicy
from .hub import NodeCommandError, NodeConnection, NodeHub, NodeTimeout, NodeUnavailable

AuditSink = Callable[[Dict[str, Any]], Awaitable[None]]
ApprovalHook = Callable[[str, Dict[str, Any]], Awaitable[bool]]


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
        policy: Optional[ServerNodePolicy] = None,
        audit: Optional[AuditSink] = None,
        approve: Optional[ApprovalHook] = None,
        skip_approval: bool = False,
    ) -> None:
        self.hub = hub
        self.node_id = node_id
        self.max_width = max_width
        self.timeout = timeout
        self.policy = policy
        self.audit = audit
        self.approve = approve
        # A run in "confirm" mode already asks the human about every tool call;
        # asking again here would just double-prompt.
        self.skip_approval = bool(skip_approval)
        self.limiter: RateLimiter = (
            policy.limiter() if policy is not None else RateLimiter(0)
        )
        connection = hub.connection(node_id)
        self.backend = connection.backend if connection is not None else "node"

    # -- plumbing --------------------------------------------------------
    @property
    def connection(self) -> Optional[NodeConnection]:
        return self.hub.connection(self.node_id)

    async def _record(
        self, name: str, params: Dict[str, Any], outcome: str, error: str = ""
    ) -> None:
        if self.audit is None:
            return
        event = describe_command(name, params)
        event.update({"outcome": outcome, "node_id": self.node_id})
        if error:
            event["error"] = str(error)[:300]
        try:
            await self.audit(event)
        except Exception:  # pragma: no cover - auditing must never break a run
            pass

    async def _authorise(self, name: str, params: Dict[str, Any]) -> None:
        """Raise :class:`ComputerError` when policy forbids this command."""
        decision = self.policy.decide(name, params) if self.policy is not None else None
        if decision is not None and decision.outcome == "deny":
            await self._record(name, params, "denied", decision.reason)
            raise ComputerError(decision.reason)

        if not self.limiter.allow():
            reason = (
                f"Rate limit reached ({self.limiter.per_minute} commands per minute), "
                f"so '{name}' was not sent to the node."
            )
            await self._record(name, params, "throttled", reason)
            raise ComputerError(reason)

        if decision is not None and decision.outcome == "confirm" and not self.skip_approval:
            if self.approve is None:
                reason = f"'{name}' needs approval, but no approval channel is available."
                await self._record(name, params, "denied", reason)
                raise ComputerError(reason)
            approved = await self.approve(name, params)
            await self._record(
                name, params, "approved" if approved else "rejected", decision.reason
            )
            if not approved:
                raise ComputerError(f"'{name}' was not approved, so nothing was run.")

    async def _call(
        self, name: str, params: Dict[str, Any], timeout: Optional[float] = None
    ) -> Dict[str, Any]:
        await self._authorise(name, params)
        try:
            data = await self.hub.dispatch(
                name, params, timeout=timeout or self.timeout, node_id=self.node_id
            )
        except (NodeUnavailable, NodeTimeout, NodeCommandError) as exc:
            await self._record(name, params, "failed", str(exc))
            raise ComputerError(str(exc)) from exc
        await self._record(name, params, "ok")
        return data

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
    async def list_apps(self, query: str = "", kind: Optional[str] = None) -> Dict[str, Any]:
        params: Dict[str, Any] = {}
        if query:
            params["query"] = query
        if kind:
            params["kind"] = kind
        # Listing is read-only: no approval prompt, but the allowlist still
        # applies and the call is rate limited like everything else.
        return await self._call("list_apps", params, timeout=15.0)

    async def open_item(
        self,
        target: str,
        kind: Optional[str] = None,
        app_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        params: Dict[str, Any] = {"target": target}
        if kind:
            params["kind"] = kind
        if app_id:
            params["app_id"] = app_id
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
            "policy": self.policy.summary() if self.policy is not None else "unrestricted",
        }
        if connection is not None:
            screen = connection.screen or {}
            info["screen_width"] = screen.get("width")
            info["screen_height"] = screen.get("height")
        return info

    async def close(self) -> None:
        """Nothing to close: the node owns the driver and the socket."""
        return None


__all__ = ["ApprovalHook", "AuditSink", "RemoteComputer"]
