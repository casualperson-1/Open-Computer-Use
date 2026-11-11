"""Outbound WebSocket client that connects this node to the server.

The node is always the one dialing out, so no inbound port is needed here.
Authentication is a single long-lived per-node token sent in the ``hello``
frame; the server answers ``welcome`` or closes with 4401.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from typing import Any, Callable, Dict, Optional

import websockets
from shared.protocol import (
    CLOSE_UNAUTHORIZED,
    MAX_STREAM_FPS,
    PROTOCOL_VERSION,
    CommandMessage,
    Heartbeat,
    HeartbeatAck,
    NodeError,
    NodeHello,
    NodeWelcome,
    ProtocolError,
    ResultMessage,
    ScreenFrame,
    ScreenStreamRequest,
    decode_message,
    encode_message,
)

from .audit import AuditLog
from .config import ConfigError, NodeConfig
from .executor import CommandExecutor
from .policy import NodePolicy
from .safety import SafetyState, write_secret_file

logger = logging.getLogger(__name__)

HANDSHAKE_TIMEOUT = 15.0
MAX_FRAME_BYTES = 32 * 1024 * 1024


class NodeAuthError(RuntimeError):
    """The server rejected this node's token; retrying will not help."""


def backoff_delay(
    attempt: int,
    base: float = 1.0,
    cap: float = 60.0,
    rng: Callable[[], float] = random.random,
) -> float:
    """Exponential backoff with jitter, clamped to ``cap``."""
    step = min(max(1, int(attempt)), 16)
    delay = min(cap, base * (2 ** (step - 1)))
    return max(0.5, delay * (0.8 + 0.4 * rng()))


class NodeClient:
    """Keeps a node connected to the server for as long as it runs."""

    def __init__(
        self,
        config: NodeConfig,
        safety: Optional[SafetyState] = None,
        executor: Optional[CommandExecutor] = None,
        *,
        policy: Optional[NodePolicy] = None,
        audit: Optional[AuditLog] = None,
    ) -> None:
        self.config = config
        self.safety = safety or SafetyState()
        self.policy = policy or NodePolicy()
        self.audit = audit or AuditLog.disabled()
        self.executor = executor or CommandExecutor(
            self.config, self.safety, policy=self.policy, audit=self.audit
        )
        self.node_id: Optional[str] = None
        self.connected = False
        self.server_protocol = ""
        self.last_error = ""
        self.commands_run = 0
        self.started_at = time.time()
        self._stop = asyncio.Event()
        self._socket: Any = None
        self._stream: Optional[asyncio.Task] = None
        self._send_lock = asyncio.Lock()

    # -- status ----------------------------------------------------------
    def status(self) -> Dict[str, Any]:
        return {
            "node_id": self.node_id,
            "name": self.config.label,
            "connected": self.connected,
            "server": self.config.websocket_url,
            "protocol": self.server_protocol or PROTOCOL_VERSION,
            "backend": self.config.backend,
            "paused": self.safety.paused,
            "stopped": self.safety.stopped,
            "policy": self.policy.summary(),
            "commands_run": self.commands_run,
            "last_error": self.last_error,
            "uptime_seconds": int(time.time() - self.started_at),
        }

    # -- lifecycle -------------------------------------------------------
    def request_stop(self, reason: str = "Stopped locally.") -> None:
        self.safety.stop(reason)
        self._stop.set()
        socket = self._socket
        if socket is not None:
            try:
                asyncio.get_running_loop().create_task(socket.close())
            except RuntimeError:  # pragma: no cover - no running loop
                pass

    async def run(self) -> int:
        """Connect and reconnect until stopped. Returns a process exit code."""
        attempt = 0
        while not self._stop.is_set():
            try:
                await self._session()
                attempt = 0
            except NodeAuthError as exc:
                self.last_error = str(exc)
                self.audit.record({"event": "auth_rejected", "error": str(exc)[:300]})
                logger.error("%s", exc)
                return 2
            except ConfigError as exc:
                self.last_error = str(exc)
                logger.error("%s", exc)
                return 2
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                attempt += 1
                self.last_error = f"{type(exc).__name__}: {exc}"
                self.audit.record(
                    {"event": "connect_failed", "attempt": attempt, "error": self.last_error[:300]}
                )
                delay = backoff_delay(attempt, cap=self.config.reconnect_max_seconds)
                logger.warning(
                    "Node connection failed (%s). Reconnecting in %.1fs (attempt %d).",
                    self.last_error,
                    delay,
                    attempt,
                )
                await self._sleep(delay)
        return 0

    async def _sleep(self, delay: float) -> None:
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=delay)
        except asyncio.TimeoutError:
            pass

    async def _session(self) -> None:
        url = self.config.websocket_url
        logger.info("Connecting to %s as %s (%s)", url, self.config.label, self.config.backend)
        async with websockets.connect(
            url,
            max_size=MAX_FRAME_BYTES,
            ping_interval=None,  # heartbeat is application level so pause is visible
            open_timeout=HANDSHAKE_TIMEOUT,
            close_timeout=5,
            user_agent_header="occ-node/1.0",
            # Cloudflare Access service token, if this node hostname is behind
            # Access. Sent as headers only; never logged or written to the audit
            # file. The node token in the hello frame is still mandatory.
            additional_headers=self.config.access_headers or None,
        ) as socket:
            self._socket = socket
            try:
                welcome = await self._handshake(socket)
                self.node_id = welcome.node_id
                self.server_protocol = welcome.protocol
                self.connected = True
                self.last_error = ""
                logger.info("Connected to the server as node %s", welcome.node_id)
                write_secret_file(self.config.node_id_path, welcome.node_id)
                self.audit.record(
                    {
                        "event": "connected",
                        "node_id": welcome.node_id,
                        "server": url,
                        "backend": self.config.backend,
                    }
                )

                heartbeat = asyncio.create_task(
                    self._heartbeat(socket, welcome.heartbeat_interval_ms / 1000.0)
                )
                try:
                    await self._receive(socket)
                finally:
                    await self._stop_stream()
                    heartbeat.cancel()
                    try:
                        await heartbeat
                    except (asyncio.CancelledError, Exception):
                        pass
            finally:
                self.connected = False
                self._socket = None
                self.audit.record(
                    {"event": "disconnected", "node_id": self.node_id, "commands_run": self.commands_run}
                )

    async def _handshake(self, socket) -> NodeWelcome:
        status = {}
        try:
            status = await self.executor.status()
        except Exception:  # pragma: no cover - defensive
            logger.debug("Could not describe the node before the handshake", exc_info=True)

        hello = NodeHello(
            protocol=PROTOCOL_VERSION,
            token=self.config.token,
            node_id=None,
            name=self.config.label,
            platform=str(status.get("platform") or ""),
            backend=str(status.get("backend") or self.config.backend),
            screen=dict(status.get("screen") or {}),
            capabilities=list(status.get("capabilities") or []),
        )
        await socket.send(encode_message(hello))

        try:
            raw = await asyncio.wait_for(socket.recv(), timeout=HANDSHAKE_TIMEOUT)
        except asyncio.TimeoutError as exc:
            raise ConnectionError("The server did not accept the handshake in time.") from exc
        except websockets.ConnectionClosed as exc:
            raise self._auth_error_from_close(exc) or exc

        try:
            message = decode_message(raw)
        except ProtocolError as exc:
            raise ConnectionError(f"The server sent an unreadable handshake: {exc}") from exc

        if isinstance(message, NodeWelcome):
            if message.protocol.split(".")[0] != PROTOCOL_VERSION.split(".")[0]:
                raise ConnectionError(
                    f"Protocol mismatch: the node speaks {PROTOCOL_VERSION}, "
                    f"the server {message.protocol}."
                )
            return message
        if isinstance(message, NodeError):
            if message.code in ("unauthorized", "revoked"):
                raise NodeAuthError(
                    f"The server rejected this node's token ({message.message or message.code}). "
                    "Create a new token in the server UI."
                )
            raise ConnectionError(message.message or "The server rejected the handshake.")
        raise ConnectionError("The server's first message was not a welcome.")

    def _auth_error_from_close(self, exc) -> Optional[NodeAuthError]:
        code = getattr(getattr(exc, "rcvd", None), "code", None)
        if code == CLOSE_UNAUTHORIZED:
            return NodeAuthError(
                "The server rejected this node's token. Create a new token in the server UI "
                "and restart the node."
            )
        return None

    async def _heartbeat(self, socket, interval: float) -> None:
        interval = max(2.0, float(interval))
        while True:
            await asyncio.sleep(interval)
            try:
                await socket.send(encode_message(Heartbeat()))
            except websockets.ConnectionClosed:
                return
            except Exception:  # pragma: no cover - defensive
                return

    async def _receive(self, socket) -> None:
        while True:
            try:
                raw = await socket.recv()
            except websockets.ConnectionClosed as exc:
                auth = self._auth_error_from_close(exc)
                if auth is not None:
                    raise auth
                return

            try:
                message = decode_message(raw)
            except ProtocolError as exc:
                logger.warning("Ignoring a bad frame from the server: %s", exc)
                continue

            if isinstance(message, CommandMessage):
                await self._run_command(socket, message)
            elif isinstance(message, ScreenStreamRequest):
                await self._set_stream(socket, message)
            elif isinstance(message, HeartbeatAck):
                continue
            elif isinstance(message, Heartbeat):
                await self._send(socket, HeartbeatAck())  # type: ignore[arg-type]
            elif isinstance(message, NodeError):
                logger.warning("Server error: %s (%s)", message.message, message.code)
            else:
                logger.debug("Ignoring unexpected message type %s", type(message).__name__)

            if self.safety.stopped:
                logger.info("Node stopped locally; closing the connection.")
                self._stop.set()
                return

    async def _run_command(self, socket, message: CommandMessage) -> None:
        started = time.monotonic()
        result: ResultMessage
        try:
            data = await asyncio.wait_for(
                self.executor.execute(message.name, message.params),
                timeout=message.timeout_ms / 1000.0,
            )
            result = ResultMessage(
                id=message.id,
                ok=True,
                data=data,
                duration_ms=int((time.monotonic() - started) * 1000),
            )
            self.commands_run += 1
        except asyncio.TimeoutError:
            result = ResultMessage(
                id=message.id,
                ok=False,
                error=f"'{message.name}' timed out on the node.",
                error_code="timeout",
                duration_ms=int((time.monotonic() - started) * 1000),
            )
        except ProtocolError as exc:
            result = ResultMessage(
                id=message.id,
                ok=False,
                error=str(exc),
                error_code=exc.code,
                duration_ms=int((time.monotonic() - started) * 1000),
            )
        except Exception as exc:
            code = getattr(exc, "code", "failed")
            result = ResultMessage(
                id=message.id,
                ok=False,
                error=str(exc),
                error_code=code,
                duration_ms=int((time.monotonic() - started) * 1000),
            )
        await self._send(socket, result)

    # -- live view -------------------------------------------------------
    async def _set_stream(self, socket, request: ScreenStreamRequest) -> None:
        """Start or stop the background frame pusher for this connection."""
        await self._stop_stream()
        if not request.enabled or self.safety.stopped:
            return
        if not self.policy.command_allowed("screenshot"):
            logger.info("Live view is not allowed by this node's policy.")
            return
        self._stream = asyncio.create_task(
            self._stream_frames(socket, request.fps, request.quality)
        )

    async def _stop_stream(self) -> None:
        task, self._stream = self._stream, None
        if task is None:
            return
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass

    async def _stream_frames(self, socket, fps: float, quality: int) -> None:
        """Push JPEG frames while the server keeps the live view subscribed."""
        interval = 1.0 / max(0.5, min(float(fps), MAX_STREAM_FPS))
        while True:
            started = time.monotonic()
            try:
                driver = await self.executor.driver()
                frame = await driver.capture()
                data = await asyncio.to_thread(
                    frame.jpeg_data_url, int(quality), self.config.max_width
                )
                await self._send(
                    socket,
                    ScreenFrame(
                        data=data,
                        width=frame.width,
                        height=frame.height,
                        screen_width=frame.screen_width,
                        screen_height=frame.screen_height,
                        cursor=[frame.cursor_x, frame.cursor_y],
                        label=frame.label,
                        backend=self.config.backend,
                    ),
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.debug("Live view frame failed", exc_info=True)
            await asyncio.sleep(
                max(0.02, interval - (time.monotonic() - started))
            )

    async def _send(self, socket, message) -> None:
        try:
            async with self._send_lock:
                await socket.send(encode_message(message))
        except websockets.ConnectionClosed:
            logger.debug("Could not send %s: the connection is closed.", type(message).__name__)

    async def close(self) -> None:
        self._stop.set()
        await self._stop_stream()
        await self.executor.close()


__all__ = ["NodeAuthError", "NodeClient", "backoff_delay"]
