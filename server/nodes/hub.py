"""Live node connections, request routing and heartbeats.

The hub is transport-only: it knows which node is connected, sends typed
commands with request ids and matches the replies. Policy (allowlists, approval,
rate limits) belongs to the layers above it.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, Iterable, List, Optional

from shared.protocol import (
    DEFAULT_TIMEOUT_MS,
    CommandMessage,
    Heartbeat,
    NodeError,
    ProtocolError,
    ResultMessage,
    encode_message,
    new_request_id,
    validate_command,
)

logger = logging.getLogger(__name__)

STALE_AFTER_SECONDS = 60.0
CLOSE_STALE = 4408


class NodeUnavailable(RuntimeError):
    """No node is connected to run the command."""


class NodeTimeout(RuntimeError):
    """The node did not answer a command in time."""


class NodeCommandError(RuntimeError):
    """The node ran the command and reported a failure."""

    def __init__(self, message: str, code: str = "failed") -> None:
        super().__init__(message)
        self.code = code


class NodeConnection:
    """One authenticated node socket."""

    def __init__(
        self,
        node_id: str,
        websocket,
        *,
        name: str = "",
        backend: str = "desktop",
        platform: str = "",
        capabilities: Optional[Iterable[str]] = None,
        screen: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.node_id = node_id
        self.websocket = websocket
        self.name = name or node_id
        self.backend = (backend or "desktop")[:20]
        self.platform = (platform or "")[:200]
        self.capabilities = list(capabilities or [])
        self.screen = dict(screen or {})
        self.connected_at = time.time()
        self.last_seen = self.connected_at
        self.closed = False
        self._pending: Dict[str, asyncio.Future] = {}
        self._send_lock = asyncio.Lock()

    # -- sending ---------------------------------------------------------
    async def send(self, message) -> None:
        text = encode_message(message)
        async with self._send_lock:
            # Starlette/uvicorn expect ``send_text`` for a string frame.
            sender = getattr(self.websocket, "send_text", None)
            if sender is not None:
                await sender(text)
            else:  # pragma: no cover - test doubles only
                await self.websocket.send(text)

    async def send_command(self, name: str, params: Dict[str, Any], timeout: float) -> Dict[str, Any]:
        if self.closed:
            raise NodeUnavailable(f"Node {self.node_id} is not connected.")
        try:
            clean = validate_command(name, params)
        except ProtocolError as exc:
            raise NodeCommandError(str(exc), code=exc.code) from exc

        request_id = new_request_id()
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        try:
            message = CommandMessage(
                id=request_id,
                name=name,
                params=clean,
                timeout_ms=int(max(0.1, min(timeout, 300.0)) * 1000),
            )
            await self.send(message)
            result = await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError as exc:
            raise NodeTimeout(f"'{name}' timed out on node {self.name}.") from exc
        finally:
            self._pending.pop(request_id, None)

        if not result.ok:
            raise NodeCommandError(
                result.error or f"'{name}' failed on the node.",
                code=result.error_code or "failed",
            )
        return dict(result.data or {})

    # -- receiving -------------------------------------------------------
    def resolve(self, message: ResultMessage) -> None:
        future = self._pending.get(message.id)
        if future is None:
            logger.debug("Dropping a result for unknown request %s", message.id)
            return
        if not future.done():
            future.set_result(message)

    def touch(self) -> None:
        self.last_seen = time.time()

    def fail_pending(self, reason: str) -> None:
        self.closed = True
        for future in list(self._pending.values()):
            if not future.done():
                future.set_exception(NodeUnavailable(reason))
        self._pending.clear()

    def summary(self) -> Dict[str, Any]:
        return {
            "connected": not self.closed,
            "node_id": self.node_id,
            "name": self.name,
            "backend": self.backend,
            "platform": self.platform,
            "capabilities": list(self.capabilities),
            "screen": dict(self.screen),
            "connected_at": self.connected_at,
            "last_seen": self.last_seen,
        }


class NodeHub:
    """All live node connections, with a single primary node for runs."""

    def __init__(self) -> None:
        self._connections: Dict[str, NodeConnection] = {}
        self._primary_id: Optional[str] = None
        self._watchdog: Optional[asyncio.Task] = None

    # -- registry --------------------------------------------------------
    async def register(self, connection: NodeConnection) -> None:
        previous = self._connections.get(connection.node_id)
        if previous is not None and previous is not connection:
            previous.fail_pending("Replaced by a newer connection.")
            try:
                await previous.websocket.close(code=4000)
            except Exception:  # pragma: no cover - already gone
                pass
        self._connections[connection.node_id] = connection
        self._primary_id = connection.node_id
        logger.info(
            "Node %s connected (%s, %s)", connection.node_id, connection.name, connection.backend
        )

    def unregister(self, connection: NodeConnection) -> None:
        if self._connections.get(connection.node_id) is connection:
            del self._connections[connection.node_id]
        connection.fail_pending("The node disconnected.")
        if self._primary_id == connection.node_id:
            self._primary_id = next(iter(self._connections), None)
        logger.info("Node %s disconnected", connection.node_id)

    def connection(self, node_id: Optional[str] = None) -> Optional[NodeConnection]:
        if node_id is not None:
            return self._connections.get(node_id)
        if self._primary_id is not None:
            return self._connections.get(self._primary_id)
        return None

    def connections(self) -> List[NodeConnection]:
        return list(self._connections.values())

    def is_connected(self) -> bool:
        return self.connection() is not None

    def summary(self) -> Dict[str, Any]:
        connection = self.connection()
        if connection is None:
            return {"connected": False, "nodes": len(self._connections)}
        payload = connection.summary()
        payload["nodes"] = len(self._connections)
        return payload

    def select(self, node_id: Optional[str]) -> None:
        if node_id is not None and node_id not in self._connections:
            raise NodeUnavailable(f"Node {node_id} is not connected.")
        self._primary_id = node_id

    # -- dispatch --------------------------------------------------------
    async def dispatch(
        self,
        name: str,
        params: Optional[Dict[str, Any]] = None,
        *,
        timeout: float = DEFAULT_TIMEOUT_MS / 1000.0,
        node_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        connection = self.connection(node_id)
        if connection is None:
            raise NodeUnavailable("No desktop node is connected.")
        return await connection.send_command(name, params or {}, timeout)

    async def broadcast(self, name: str, params: Optional[Dict[str, Any]] = None) -> List[str]:
        """Send a control command to every connected node; returns the failures."""
        failures: List[str] = []
        for connection in self.connections():
            try:
                await connection.send_command(name, params or {}, timeout=10.0)
            except Exception as exc:
                failures.append(f"{connection.name}: {exc}")
        return failures

    async def disconnect_all(self, reason: str = "The server is shutting down.") -> None:
        for connection in self.connections():
            connection.fail_pending(reason)
            try:
                await connection.websocket.close(code=1001)
            except Exception:  # pragma: no cover - already gone
                pass
        self._connections.clear()
        self._primary_id = None

    # -- watchdog --------------------------------------------------------
    def start_watchdog(self, interval: float = 15.0) -> None:
        if self._watchdog is None or self._watchdog.done():
            self._watchdog = asyncio.create_task(self._watch(interval))

    async def stop_watchdog(self) -> None:
        task, self._watchdog = self._watchdog, None
        if task is not None:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

    async def _watch(self, interval: float) -> None:
        while True:
            await asyncio.sleep(interval)
            now = time.time()
            for connection in self.connections():
                if now - connection.last_seen <= STALE_AFTER_SECONDS:
                    continue
                logger.warning(
                    "Node %s missed heartbeats for %.0fs; dropping the connection.",
                    connection.node_id,
                    now - connection.last_seen,
                )
                try:
                    await connection.websocket.close(code=CLOSE_STALE)
                except Exception:  # pragma: no cover - already gone
                    pass
                self.unregister(connection)


hub = NodeHub()


def reject(websocket, code: str, message: str) -> NodeError:
    return NodeError(code=code, message=message)


def heartbeat_ack(ts: Optional[float] = None) -> Heartbeat:
    return Heartbeat(ts=ts or time.time())


__all__ = [
    "NodeCommandError",
    "NodeConnection",
    "NodeHub",
    "NodeTimeout",
    "NodeUnavailable",
    "STALE_AFTER_SECONDS",
    "heartbeat_ack",
    "hub",
    "reject",
]
