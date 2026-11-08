"""Pause/resume/stop state shared by the socket, the control page and the CLI.

The state is thread-safe because the loopback control page runs on its own
thread while the WebSocket client runs on the asyncio loop.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Optional


class NodeHalted(RuntimeError):
    """Raised when a command arrives while the node is paused or stopped."""

    code = "halted"

    def __init__(self, message: str, code: str = "halted") -> None:
        super().__init__(message)
        self.code = code


class SafetyState:
    """The node's local override switch.

    ``stop`` is sticky: only restarting the node clears it, so a kill switch
    cannot be undone by a remote command.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._paused = False
        self._stopped = False
        self._reason = ""

    # -- state -----------------------------------------------------------
    @property
    def paused(self) -> bool:
        with self._lock:
            return self._paused

    @property
    def stopped(self) -> bool:
        with self._lock:
            return self._stopped

    @property
    def reason(self) -> str:
        with self._lock:
            return self._reason

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "paused": self._paused,
                "stopped": self._stopped,
                "reason": self._reason,
            }

    # -- transitions -----------------------------------------------------
    def pause(self, reason: str = "Paused at the node.") -> bool:
        with self._lock:
            if self._stopped:
                return False
            self._paused = True
            self._reason = reason[:200]
            return True

    def resume(self) -> bool:
        with self._lock:
            if self._stopped:
                return False
            self._paused = False
            self._reason = ""
            return True

    def stop(self, reason: str = "Stopped at the node.") -> bool:
        with self._lock:
            self._stopped = True
            self._paused = True
            self._reason = reason[:200]
            return True

    def check(self) -> None:
        """Raise when the node must not run a command right now."""
        with self._lock:
            if self._stopped:
                raise NodeHalted(
                    self._reason or "The node has been stopped locally.", code="stopped"
                )
            if self._paused:
                raise NodeHalted(
                    self._reason or "The node is paused locally.", code="paused"
                )


def write_secret_file(path: Path, value: str) -> None:
    """Write a credential readable only by the current user."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as handle:
        handle.write(value)
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, path)


def read_secret_file(path: Path) -> Optional[str]:
    try:
        value = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return value or None


def read_pid(path: Path) -> Optional[int]:
    value = read_secret_file(path)
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        return None


__all__ = [
    "NodeHalted",
    "SafetyState",
    "read_pid",
    "read_secret_file",
    "write_secret_file",
]
